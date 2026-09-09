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
"""Plugin for the datanode block-deletion-stuck alert.

Prometheus alertname: ``OzoneDatanodeDeletionNotProgressing``. Severity tiers
(low/medium/high/critical) come from how long the condition has held --
see ozone-aiops-alerts.yml.
"""

import logging
from typing import List, Tuple

from app.collectors.endpoints import resolve_http_address
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins._deletion_common import fetch_config_properties, fetch_jmx_metrics, halve_duration
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.registry import register_plugin

logger = logging.getLogger(__name__)

DN_BLOCK_DELETING_JMX_QUERY = "Hadoop:service=HddsDatanode,name=BlockDeletingService"

DECREASE_DN_BLOCK_DELETING_INTERVAL = "decrease_datanode_block_deleting_interval"
DEFAULT_DN_BLOCK_DELETING_INTERVAL = "60s"

DN_CONFIG_PROPERTIES = (
    "ozone.block.deleting.service.interval",
    "ozone.block.deleting.container.limit.per.interval",
    "ozone.block.deleting.service.workers",
    "ozone.block.deleting.service.timeout",
)

DN_JMX_KEYS = (
    "TotalPendingBlockCount",
    "TotalPendingBlockBytes",
    "SuccessCount",
    "FailureCount",
    "ProcessedTransactionSuccessCount",
    "ProcessedTransactionFailCount",
    "ReceivedTransactionCount",
    "TotalLockTimeoutTransactionCount",
)


@register_plugin()
class DatanodeDeletionNotProgressingPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneDatanodeDeletionNotProgressing"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        http_address, endpoint_note = resolve_http_address(alert, cluster)
        notes: List[str] = []
        if endpoint_note:
            notes.append(endpoint_note)
        notes.append("Deletion hop under diagnosis: datanode")

        logger.info("Datanode plugin: resolved http_address=%r endpoint_note=%r", http_address, endpoint_note)
        if not http_address:
            notes.append("No HTTP endpoint available for evidence collection.")
            return DiagnosticContext(alert=alert, notes=notes)

        instance = alert.labels.get("instance", http_address)
        jmx_metrics = fetch_jmx_metrics(
            http_address,
            DN_BLOCK_DELETING_JMX_QUERY,
            DN_JMX_KEYS,
            notes,
            missing_bean_note=(
                f"BlockDeletingService MBean was not found on datanode {instance} "
                "-- the local block deletion service may not have started."
            ),
            endpoint_label=f"datanode {instance}",
        )
        config_properties = fetch_config_properties(
            http_address, DN_CONFIG_PROPERTIES, notes, f"datanode {instance}"
        )
        logger.info(
            "Datanode plugin: collect_context -> jmx_metrics=%s config_properties=%s notes=%s",
            jmx_metrics, config_properties, notes,
        )
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        metrics = context.jmx_metrics
        if not metrics:
            return False, (
                "Could not collect datanode block-deleting-service JMX metrics, so "
                "the backlog claimed by the alert could not be independently verified."
            )

        pending = metrics.get("TotalPendingBlockCount")
        if isinstance(pending, (int, float)):
            if pending <= 0:
                return False, (
                    "TotalPendingBlockCount is 0; this datanode has no pending "
                    "block deletions right now, so this alert may be stale or "
                    "already resolved."
                )
            return True, (
                f"TotalPendingBlockCount={pending} confirms blocks are still "
                "waiting to be deleted on this datanode."
            )

        return True, (
            "Datanode JMX metrics were collected but did not include the specific "
            "counters needed to confirm backlog size; treating the alert as "
            "unverified-but-plausible."
        )

    def retrieval_query(self, context: DiagnosticContext) -> str:
        pending = context.jmx_metrics.get("TotalPendingBlockCount", "unknown")
        success = context.jmx_metrics.get("SuccessCount", "unknown")
        interval = context.config_properties.get(
            "ozone.block.deleting.service.interval", "unknown"
        )
        instance = context.alert.labels.get("instance", "unknown")
        return (
            f"Ozone datanode {instance} block deletion not progressing. "
            f"totalPendingBlockCount={pending} successCount={success} "
            f"ozone.block.deleting.service.interval={interval}. "
            "BlockDeletingService, container lock timeouts, DN disk issues."
        )

    def permitted_actions(self) -> List[ActionSpec]:
        logger.info("Datanode plugin: permitted_actions -> %s", DECREASE_DN_BLOCK_DELETING_INTERVAL)
        return [
            ActionSpec(
                action_id=DECREASE_DN_BLOCK_DELETING_INTERVAL,
                description=(
                    "Halve ozone.block.deleting.service.interval on the affected "
                    "datanode so BlockDeletingService runs more frequently."
                ),
                config_property="ozone.block.deleting.service.interval",
                risk="medium",
                requires_restart=False,
            ),
        ]

    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        if action_id != DECREASE_DN_BLOCK_DELETING_INTERVAL:
            raise ValueError(
                f"action_id={action_id!r} is not valid for the datanode deletion "
                f"plugin. Expected {DECREASE_DN_BLOCK_DELETING_INTERVAL!r}."
            )

        prop = "ozone.block.deleting.service.interval"
        current_value = context.config_properties.get(prop) or DEFAULT_DN_BLOCK_DELETING_INTERVAL
        proposed_value = halve_duration(current_value)
        instance = context.alert.labels.get("instance", "affected datanode")
        warnings = list(context.notes)
        if prop not in context.config_properties:
            warnings.append(
                "Could not read the current datanode interval from the cluster; "
                "the plan assumes the ozone-default.xml default and should be "
                "double-checked."
            )
        return RemediationPlan(
            action_id=DECREASE_DN_BLOCK_DELETING_INTERVAL,
            description=(
                f"Set {prop} from {current_value} to {proposed_value} on datanode "
                f"{instance} (reconfigurable without restart)."
            ),
            config_changes={prop: proposed_value},
            requires_restart=False,
            risk="medium",
            dry_run=True,
            applied=False,
            warnings=warnings,
        )
