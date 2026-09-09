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
"""Plugin for OM, SCM, and datanode deletion-stuck alerts.

Prometheus alertnames: ``OzoneOmDeletionNotProgressing``,
``OzoneScmDeletionNotProgressing``, ``OzoneDatanodeDeletionNotProgressing``.
Each has low/medium/high/critical severity tiers (see ozone-aiops-alerts.yml).
"""

from typing import Dict, List, Tuple

from app.collectors.config_client import ConfigFetchError, fetch_properties
from app.collectors.endpoints import (
    COMPONENT_DATANODE,
    COMPONENT_OM,
    COMPONENT_SCM,
    deletion_component,
    resolve_http_address,
)
from app.collectors.jmx_client import JmxFetchError, fetch_bean, pick_metrics
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.duration_utils import halve_duration
from app.plugins.registry import register_plugin

OM_DELETING_SERVICE_JMX_QUERY = "Hadoop:service=OzoneManager,name=DeletingServiceMetrics"
SCM_BLOCK_DELETING_JMX_QUERY = (
    "Hadoop:service=StorageContainerManager,name=SCMBlockDeletingService"
)
DN_BLOCK_DELETING_JMX_QUERY = "Hadoop:service=HddsDatanode,name=BlockDeletingService"

INCREASE_KEY_DELETING_LIMIT = "increase_key_deleting_limit_per_task"
INCREASE_SCM_BLOCK_DELETION_LIMIT = "increase_scm_block_deletion_per_interval_max"
DECREASE_DN_BLOCK_DELETING_INTERVAL = "decrease_datanode_block_deleting_interval"

DEFAULT_KEY_DELETING_LIMIT_PER_TASK = 50000
DEFAULT_SCM_BLOCK_DELETION_PER_INTERVAL_MAX = 500000
DEFAULT_DN_BLOCK_DELETING_INTERVAL = "60s"

OM_CONFIG_PROPERTIES = (
    "ozone.key.deleting.limit.per.task",
    "ozone.snapshot.key.deleting.limit.per.task",
    "ozone.block.deleting.service.interval",
    "ozone.block.deleting.container.limit.per.interval",
    "ozone.snapshot.deep.cleaning.enabled",
)

SCM_CONFIG_PROPERTIES = (
    "hdds.scm.block.deletion.per-interval.max",
    "hdds.scm.block.deleting.service.interval",
    "hdds.scm.block.deletion.txn.dn.commit.map.limit",
)

DN_CONFIG_PROPERTIES = (
    "ozone.block.deleting.service.interval",
    "ozone.block.deleting.container.limit.per.interval",
    "ozone.block.deleting.service.workers",
    "ozone.block.deleting.service.timeout",
)

OM_JMX_KEYS = (
    "NumKeysProcessed",
    "NumKeysSentForPurge",
    "NumKeysPurged",
    "NumDirsSentForPurge",
    "NumDirsPurged",
    "MetricsResetTimeStamp",
)

SCM_JMX_KEYS = (
    "NumBlockDeletionCommandSent",
    "NumBlockDeletionCommandSuccess",
    "NumBlockDeletionCommandFailure",
    "NumBlockDeletionTransactionsOnDatanodes",
    "NumBlockDeletionTransactionSuccessOnDatanodes",
    "NumBlockDeletionTransactionFailureOnDatanodes",
    "NumBlockDeletionTransactionCompleted",
    "NumBlockDeletionTransactionCreated",
    "numBlockDeletionTransactions",
    "numBlockOfAllDeletionTransactions",
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

_COMPONENT_ACTIONS: Dict[str, str] = {
    COMPONENT_OM: INCREASE_KEY_DELETING_LIMIT,
    COMPONENT_SCM: INCREASE_SCM_BLOCK_DELETION_LIMIT,
    COMPONENT_DATANODE: DECREASE_DN_BLOCK_DELETING_INTERVAL,
}


@register_plugin(
    "OzoneOmDeletionNotProgressing",
    "OzoneScmDeletionNotProgressing",
    "OzoneDatanodeDeletionNotProgressing",
)
class DeletionNotProgressingPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneOmDeletionNotProgressing"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        component = deletion_component(alert)
        http_address, endpoint_note = resolve_http_address(alert, cluster)
        notes: List[str] = []
        if endpoint_note:
            notes.append(endpoint_note)
        notes.append(f"Deletion hop under diagnosis: {component}")

        if not http_address:
            notes.append("No HTTP endpoint available for evidence collection.")
            return DiagnosticContext(alert=alert, notes=notes)

        if component == COMPONENT_OM:
            return self._collect_om_context(alert, http_address, notes)
        if component == COMPONENT_SCM:
            return self._collect_scm_context(alert, http_address, notes)
        return self._collect_datanode_context(alert, http_address, notes)

    def _collect_om_context(
        self, alert: AlertPayload, http_address: str, notes: List[str]
    ) -> DiagnosticContext:
        jmx_metrics = self._fetch_jmx(
            http_address,
            OM_DELETING_SERVICE_JMX_QUERY,
            OM_JMX_KEYS,
            notes,
            missing_bean_note=(
                "DeletingServiceMetrics MBean was not found on the OM JMX "
                "endpoint -- the key deletion service may not have started."
            ),
            endpoint_label="OM",
        )
        config_properties = self._fetch_config(http_address, OM_CONFIG_PROPERTIES, notes, "OM")
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    def _collect_scm_context(
        self, alert: AlertPayload, http_address: str, notes: List[str]
    ) -> DiagnosticContext:
        jmx_metrics = self._fetch_jmx(
            http_address,
            SCM_BLOCK_DELETING_JMX_QUERY,
            SCM_JMX_KEYS,
            notes,
            missing_bean_note=(
                "SCMBlockDeletingService MBean was not found on the SCM JMX "
                "endpoint -- the block deletion service may not have started."
            ),
            endpoint_label="SCM",
        )
        config_properties = self._fetch_config(http_address, SCM_CONFIG_PROPERTIES, notes, "SCM")
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    def _collect_datanode_context(
        self, alert: AlertPayload, http_address: str, notes: List[str]
    ) -> DiagnosticContext:
        instance = alert.labels.get("instance", http_address)
        jmx_metrics = self._fetch_jmx(
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
        config_properties = self._fetch_config(
            http_address, DN_CONFIG_PROPERTIES, notes, f"datanode {instance}"
        )
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    def _fetch_jmx(
        self,
        http_address: str,
        jmx_query: str,
        keys: tuple,
        notes: List[str],
        missing_bean_note: str,
        endpoint_label: str,
    ) -> Dict[str, object]:
        try:
            bean = fetch_bean(http_address, qry=jmx_query)
            if not bean:
                notes.append(missing_bean_note)
                return {}
            return pick_metrics(bean, keys)
        except JmxFetchError as exc:
            notes.append(f"Could not reach {endpoint_label} JMX endpoint: {exc}")
            return {}

    def _fetch_config(
        self,
        http_address: str,
        property_names: tuple,
        notes: List[str],
        endpoint_label: str,
    ) -> Dict[str, str]:
        try:
            return fetch_properties(http_address, property_names)
        except ConfigFetchError as exc:
            notes.append(f"Could not reach {endpoint_label} config endpoint: {exc}")
            return {}

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        component = deletion_component(context.alert)
        metrics = context.jmx_metrics

        if not metrics:
            return False, (
                f"Could not collect {component} deletion-service JMX metrics, so the "
                "backlog claimed by the alert could not be independently verified."
            )

        if component == COMPONENT_SCM:
            pending = metrics.get("numBlockDeletionTransactions")
            completed = metrics.get("NumBlockDeletionTransactionCompleted")
            if isinstance(pending, (int, float)) and isinstance(completed, (int, float)):
                backlog = pending - completed
                if backlog <= 0:
                    return False, (
                        f"numBlockDeletionTransactions ({pending}) is not ahead of "
                        f"NumBlockDeletionTransactionCompleted ({completed}); SCM has no "
                        "outstanding block-deletion backlog right now, so this alert may "
                        "be stale or already resolved."
                    )
                return True, (
                    f"SCM has {backlog} block-deletion transaction(s) pending completion "
                    f"(numBlockDeletionTransactions={pending}, "
                    f"NumBlockDeletionTransactionCompleted={completed}), confirming a backlog."
                )

        elif component == COMPONENT_DATANODE:
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

        else:
            processed = metrics.get("NumKeysProcessed")
            purged = metrics.get("NumKeysPurged")
            if isinstance(processed, (int, float)) and isinstance(purged, (int, float)):
                backlog = processed - purged
                if backlog <= 0:
                    return False, (
                        f"NumKeysProcessed ({processed}) is not ahead of NumKeysPurged "
                        f"({purged}); OM has no outstanding key-deletion backlog right "
                        "now, so this alert may be stale or already resolved."
                    )
                return True, (
                    f"OM has {backlog} key(s) processed but not yet purged "
                    f"(NumKeysProcessed={processed}, NumKeysPurged={purged}), "
                    "confirming a backlog."
                )

        return True, (
            f"{component} JMX metrics were collected but did not include the "
            "specific counters needed to confirm backlog size; treating the "
            "alert as unverified-but-plausible."
        )

    def retrieval_query(self, context: DiagnosticContext) -> str:
        component = deletion_component(context.alert)
        if component == COMPONENT_SCM:
            pending = context.jmx_metrics.get("numBlockDeletionTransactions", "unknown")
            completed = context.jmx_metrics.get("NumBlockDeletionTransactionCompleted", "unknown")
            limit = context.config_properties.get(
                "hdds.scm.block.deletion.per-interval.max", "unknown"
            )
            return (
                "Ozone SCM block deletion backlog not draining. "
                f"numBlockDeletionTransactions={pending} "
                f"NumBlockDeletionTransactionCompleted={completed} "
                f"hdds.scm.block.deletion.per-interval.max={limit}. "
                "DeletedBlockLog, datanode deletion command acks, SCM block "
                "deleting service interval."
            )

        if component == COMPONENT_DATANODE:
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

        processed = context.jmx_metrics.get("NumKeysProcessed", "unknown")
        purged = context.jmx_metrics.get("NumKeysPurged", "unknown")
        limit = context.config_properties.get("ozone.key.deleting.limit.per.task", "unknown")
        return (
            "Ozone OM key deletion service (KeyDeletingService) not progressing. "
            f"NumKeysProcessed={processed} NumKeysPurged={purged} "
            f"ozone.key.deleting.limit.per.task={limit}. "
            "Deep cleaning, snapshot chain, deleted table backlog, "
            "block deletion pipeline."
        )

    def permitted_actions_for(self, context: DiagnosticContext) -> List[ActionSpec]:
        action_id = _COMPONENT_ACTIONS[deletion_component(context.alert)]
        return [action for action in self.permitted_actions() if action.action_id == action_id]

    def permitted_actions(self) -> List[ActionSpec]:
        return [
            ActionSpec(
                action_id=INCREASE_KEY_DELETING_LIMIT,
                description=(
                    "Double ozone.key.deleting.limit.per.task so KeyDeletingService "
                    "scans more keys per run on the OM."
                ),
                config_property="ozone.key.deleting.limit.per.task",
                risk="medium",
                requires_restart=True,
            ),
            ActionSpec(
                action_id=INCREASE_SCM_BLOCK_DELETION_LIMIT,
                description=(
                    "Double hdds.scm.block.deletion.per-interval.max so SCM sends "
                    "more block-deletion commands to datanodes per interval."
                ),
                config_property="hdds.scm.block.deletion.per-interval.max",
                risk="medium",
                requires_restart=False,
            ),
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
        component = deletion_component(context.alert)
        expected_action = _COMPONENT_ACTIONS.get(component)
        if action_id != expected_action:
            raise ValueError(
                f"action_id={action_id!r} is not valid for deletion hop "
                f"{component!r}. Expected {expected_action!r}."
            )

        if action_id == INCREASE_KEY_DELETING_LIMIT:
            return self._plan_increase_om_key_limit(context)
        if action_id == INCREASE_SCM_BLOCK_DELETION_LIMIT:
            return self._plan_increase_scm_block_limit(context)
        if action_id == DECREASE_DN_BLOCK_DELETING_INTERVAL:
            return self._plan_decrease_dn_interval(context)
        raise ValueError(f"Unsupported action_id for this plugin: {action_id!r}")

    def _plan_increase_om_key_limit(self, context: DiagnosticContext) -> RemediationPlan:
        current_value = context.config_properties.get("ozone.key.deleting.limit.per.task")
        current_limit = (
            int(current_value) if current_value else DEFAULT_KEY_DELETING_LIMIT_PER_TASK
        )
        proposed_limit = current_limit * 2
        warnings = list(context.notes)
        if not current_value:
            warnings.append(
                "Could not read the current value from the cluster; the plan below "
                "assumes the ozone-default.xml default and should be double-checked."
            )
        return RemediationPlan(
            action_id=INCREASE_KEY_DELETING_LIMIT,
            description=(
                f"Set ozone.key.deleting.limit.per.task from {current_limit} to "
                f"{proposed_limit} on the Ozone Manager(s)."
            ),
            config_changes={"ozone.key.deleting.limit.per.task": str(proposed_limit)},
            requires_restart=True,
            risk="medium",
            dry_run=True,
            applied=False,
            warnings=warnings,
        )

    def _plan_increase_scm_block_limit(self, context: DiagnosticContext) -> RemediationPlan:
        prop = "hdds.scm.block.deletion.per-interval.max"
        current_value = context.config_properties.get(prop)
        current_limit = (
            int(current_value) if current_value else DEFAULT_SCM_BLOCK_DELETION_PER_INTERVAL_MAX
        )
        proposed_limit = current_limit * 2
        warnings = list(context.notes)
        if not current_value:
            warnings.append(
                "Could not read the current SCM value from the cluster; the plan "
                "assumes the ozone-default.xml default and should be double-checked."
            )
        return RemediationPlan(
            action_id=INCREASE_SCM_BLOCK_DELETION_LIMIT,
            description=(
                f"Set {prop} from {current_limit} to {proposed_limit} on SCM "
                "(reconfigurable without restart)."
            ),
            config_changes={prop: str(proposed_limit)},
            requires_restart=False,
            risk="medium",
            dry_run=True,
            applied=False,
            warnings=warnings,
        )

    def _plan_decrease_dn_interval(self, context: DiagnosticContext) -> RemediationPlan:
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
