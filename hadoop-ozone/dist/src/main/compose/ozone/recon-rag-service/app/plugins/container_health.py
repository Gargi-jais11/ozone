# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Plugin for SCM container-health alerts backed by ReplicationManagerMetrics.

Prometheus alertnames: ``OzoneScmContainerMissing``,
``OzoneScmContainerUnderReplicated``, ``OzoneScmContainerUnhealthy``.
Each has low/medium/high/critical severity tiers (see ozone-aiops-alerts.yml),
driven off SCM's ReplicationManagerMetrics container-health gauges.

Unlike the deletion-not-progressing plugin, only ``under_replicated`` has an
automated remediation: a container reported ``missing`` (no online replicas)
or ``unhealthy`` (inconsistent replica states) needs operator investigation
into the underlying datanodes/replicas -- there is no config change that
safely "fixes" data that may already be lost or inconsistent, so those two
health states never propose a recommended_fix.
"""

from typing import Dict, List, Tuple

from app.collectors.config_client import ConfigFetchError, fetch_properties
from app.collectors.jmx_client import JmxFetchError, fetch_bean, pick_metrics
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.duration_utils import halve_duration
from app.plugins.registry import register_plugin

REPLICATION_MANAGER_JMX_QUERY = "Hadoop:service=StorageContainerManager,name=ReplicationManagerMetrics"

HEALTH_STATE_MISSING = "missing"
HEALTH_STATE_UNDER_REPLICATED = "under_replicated"
HEALTH_STATE_UNHEALTHY = "unhealthy"

ALERT_NAME_TO_HEALTH_STATE = {
    "OzoneScmContainerMissing": HEALTH_STATE_MISSING,
    "OzoneScmContainerUnderReplicated": HEALTH_STATE_UNDER_REPLICATED,
    "OzoneScmContainerUnhealthy": HEALTH_STATE_UNHEALTHY,
}

# ContainerHealthState metric names (see ContainerHealthState.java) that back
# each alert, plus queue-depth gauges kept as supporting evidence for all
# three health states.
_HEALTH_STATE_METRIC_KEY = {
    HEALTH_STATE_MISSING: "MissingContainers",
    HEALTH_STATE_UNDER_REPLICATED: "UnderReplicatedContainers",
    HEALTH_STATE_UNHEALTHY: "UnhealthyContainers",
}

REPLICATION_MANAGER_JMX_KEYS = (
    "MissingContainers",
    "UnderReplicatedContainers",
    "UnhealthyContainers",
    "OverReplicatedContainers",
    "UnderReplicatedQueueSize",
    "OverReplicatedQueueSize",
)

REPLICATION_MANAGER_CONFIG_PROPERTIES = (
    "hdds.scm.replication.under.replicated.interval",
    "hdds.scm.replication.thread.interval",
    "hdds.scm.replication.event.timeout",
    "hdds.scm.replication.datanode.replication.limit",
)

INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY = "increase_under_replicated_queue_processing_frequency"
DEFAULT_UNDER_REPLICATED_INTERVAL = "30s"

UNDER_REPLICATED_INTERVAL_PROPERTY = "hdds.scm.replication.under.replicated.interval"


def _health_state(alert: AlertPayload) -> str:
    mapped = ALERT_NAME_TO_HEALTH_STATE.get(alert.alert_type)
    if mapped:
        return mapped
    component = alert.labels.get("health_state", HEALTH_STATE_UNDER_REPLICATED).lower()
    return component if component in _HEALTH_STATE_METRIC_KEY else HEALTH_STATE_UNDER_REPLICATED


@register_plugin(
    "OzoneScmContainerMissing",
    "OzoneScmContainerUnhealthy",
)
class ContainerHealthPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneScmContainerUnderReplicated"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        health_state = _health_state(alert)
        http_address, endpoint_note = self._resolve_http_address(alert, cluster)
        notes: List[str] = []
        if endpoint_note:
            notes.append(endpoint_note)
        notes.append(f"Container health signal under diagnosis: {health_state}")

        if not http_address:
            notes.append("No HTTP endpoint available for evidence collection.")
            return DiagnosticContext(alert=alert, notes=notes)

        jmx_metrics = self._fetch_jmx(http_address, notes)
        config_properties = self._fetch_config(http_address, notes)
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    @staticmethod
    def _resolve_http_address(
        alert: AlertPayload, cluster: ClusterEndpoints
    ) -> Tuple[str, str]:
        instance = alert.labels.get("instance", "").strip()
        if instance:
            return instance, ""
        return cluster.scm_http_address, (
            "Alert labels did not include instance; using SCM_HTTP_ADDRESS default."
        )

    @staticmethod
    def _fetch_jmx(http_address: str, notes: List[str]) -> Dict[str, object]:
        try:
            bean = fetch_bean(http_address, qry=REPLICATION_MANAGER_JMX_QUERY)
            if not bean:
                notes.append(
                    "ReplicationManagerMetrics MBean was not found on the SCM JMX "
                    "endpoint -- SCM may not have finished startup/safemode."
                )
                return {}
            return pick_metrics(bean, REPLICATION_MANAGER_JMX_KEYS)
        except JmxFetchError as exc:
            notes.append(f"Could not reach SCM JMX endpoint: {exc}")
            return {}

    @staticmethod
    def _fetch_config(http_address: str, notes: List[str]) -> Dict[str, str]:
        try:
            return fetch_properties(http_address, REPLICATION_MANAGER_CONFIG_PROPERTIES)
        except ConfigFetchError as exc:
            notes.append(f"Could not reach SCM config endpoint: {exc}")
            return {}

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        health_state = _health_state(context.alert)
        metrics = context.jmx_metrics

        if not metrics:
            return False, (
                "Could not collect SCM ReplicationManagerMetrics, so the container "
                "backlog claimed by the alert could not be independently verified."
            )

        metric_key = _HEALTH_STATE_METRIC_KEY[health_state]
        count = metrics.get(metric_key)
        if not isinstance(count, (int, float)):
            return True, (
                f"ReplicationManagerMetrics was collected but did not include "
                f"{metric_key}; treating the alert as unverified-but-plausible."
            )
        if count <= 0:
            return False, (
                f"{metric_key} is currently 0; SCM reports no containers in this "
                "health state right now, so this alert may be stale or already resolved."
            )
        return True, f"{metric_key}={count} confirms containers are currently affected."

    def retrieval_query(self, context: DiagnosticContext) -> str:
        health_state = _health_state(context.alert)
        metric_key = _HEALTH_STATE_METRIC_KEY[health_state]
        count = context.jmx_metrics.get(metric_key, "unknown")
        under_replicated_queue = context.jmx_metrics.get("UnderReplicatedQueueSize", "unknown")
        interval = context.config_properties.get(UNDER_REPLICATED_INTERVAL_PROPERTY, "unknown")
        return (
            f"Ozone SCM container health: {health_state} containers not clearing. "
            f"{metric_key}={count} UnderReplicatedQueueSize={under_replicated_queue} "
            f"{UNDER_REPLICATED_INTERVAL_PROPERTY}={interval}. "
            "ReplicationManager, container replicas, datanode availability, "
            "under-replicated queue, missing containers, unhealthy containers."
        )

    def permitted_actions_for(self, context: DiagnosticContext) -> List[ActionSpec]:
        if _health_state(context.alert) == HEALTH_STATE_UNDER_REPLICATED:
            return self.permitted_actions()
        return []

    def permitted_actions(self) -> List[ActionSpec]:
        return [
            ActionSpec(
                action_id=INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY,
                description=(
                    "Halve hdds.scm.replication.under.replicated.interval so "
                    "ReplicationManager processes the under-replicated queue "
                    "more frequently."
                ),
                config_property=UNDER_REPLICATED_INTERVAL_PROPERTY,
                risk="medium",
                requires_restart=False,
            ),
        ]

    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        if _health_state(context.alert) != HEALTH_STATE_UNDER_REPLICATED:
            raise ValueError(
                f"action_id={action_id!r} is not valid for container health state "
                f"{_health_state(context.alert)!r}: missing/unhealthy containers "
                "require operator investigation, not an automated config change."
            )
        if action_id != INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY:
            raise ValueError(f"Unsupported action_id for this plugin: {action_id!r}")

        current_value = (
            context.config_properties.get(UNDER_REPLICATED_INTERVAL_PROPERTY)
            or DEFAULT_UNDER_REPLICATED_INTERVAL
        )
        proposed_value = halve_duration(current_value)
        warnings = list(context.notes)
        if UNDER_REPLICATED_INTERVAL_PROPERTY not in context.config_properties:
            warnings.append(
                "Could not read the current interval from the cluster; the plan "
                "assumes the ozone-default.xml default and should be double-checked."
            )
        return RemediationPlan(
            action_id=INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY,
            description=(
                f"Set {UNDER_REPLICATED_INTERVAL_PROPERTY} from {current_value} to "
                f"{proposed_value} on SCM (reconfigurable without restart)."
            ),
            config_changes={UNDER_REPLICATED_INTERVAL_PROPERTY: proposed_value},
            requires_restart=False,
            risk="medium",
            dry_run=True,
            applied=False,
            warnings=warnings,
        )
