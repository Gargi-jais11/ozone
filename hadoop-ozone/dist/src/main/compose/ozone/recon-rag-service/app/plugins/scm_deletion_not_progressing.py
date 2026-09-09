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
"""Plugin for the SCM block-deletion-backlog alert.

Prometheus alertname: ``OzoneScmDeletionNotProgressing``. Severity tiers
(low/medium/high/critical) come from how long the condition has held --
see ozone-aiops-alerts.yml.
"""

import logging
from typing import List, Tuple

from app.collectors.endpoints import resolve_http_address
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins._deletion_common import fetch_config_properties, fetch_jmx_metrics
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.registry import register_plugin

logger = logging.getLogger(__name__)

SCM_BLOCK_DELETING_JMX_QUERY = (
    "Hadoop:service=StorageContainerManager,name=SCMBlockDeletingService"
)

INCREASE_SCM_BLOCK_DELETION_LIMIT = "increase_scm_block_deletion_per_interval_max"
DEFAULT_SCM_BLOCK_DELETION_PER_INTERVAL_MAX = 500000

SCM_CONFIG_PROPERTIES = (
    "hdds.scm.block.deletion.per-interval.max",
    "hdds.scm.block.deleting.service.interval",
    "hdds.scm.block.deletion.txn.dn.commit.map.limit",
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
    "NumBlockDeletionTransactions",
    "NumBlockOfAllDeletionTransactions",
)


@register_plugin()
class ScmDeletionNotProgressingPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneScmDeletionNotProgressing"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        http_address, endpoint_note = resolve_http_address(alert, cluster)
        notes: List[str] = []
        if endpoint_note:
            notes.append(endpoint_note)
        notes.append("Deletion hop under diagnosis: scm")

        logger.info("SCM plugin: resolved http_address=%r endpoint_note=%r", http_address, endpoint_note)
        if not http_address:
            notes.append("No HTTP endpoint available for evidence collection.")
            return DiagnosticContext(alert=alert, notes=notes)

        jmx_metrics = fetch_jmx_metrics(
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
        config_properties = fetch_config_properties(
            http_address, SCM_CONFIG_PROPERTIES, notes, "SCM"
        )
        logger.info(
            "SCM plugin: collect_context -> jmx_metrics=%s config_properties=%s notes=%s",
            jmx_metrics, config_properties, notes,
        )
        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        metrics = context.jmx_metrics
        if not metrics:
            return False, (
                "Could not collect SCM block-deleting-service JMX metrics, so the "
                "backlog claimed by the alert could not be independently verified."
            )

        pending = metrics.get("NumBlockDeletionTransactions")
        completed = metrics.get("NumBlockDeletionTransactionCompleted")
        if isinstance(pending, (int, float)) and isinstance(completed, (int, float)):
            backlog = pending - completed
            if backlog <= 0:
                return False, (
                    f"NumBlockDeletionTransactions ({pending}) is not ahead of "
                    f"NumBlockDeletionTransactionCompleted ({completed}); SCM has no "
                    "outstanding block-deletion backlog right now, so this alert may "
                    "be stale or already resolved."
                )
            return True, (
                f"SCM has {backlog} block-deletion transaction(s) pending completion "
                f"(NumBlockDeletionTransactions={pending}, "
                f"NumBlockDeletionTransactionCompleted={completed}), confirming a backlog."
            )

        return True, (
            "SCM JMX metrics were collected but did not include the specific "
            "counters needed to confirm backlog size; treating the alert as "
            "unverified-but-plausible."
        )

    def retrieval_query(self, context: DiagnosticContext) -> str:
        pending = context.jmx_metrics.get("NumBlockDeletionTransactions", "unknown")
        completed = context.jmx_metrics.get("NumBlockDeletionTransactionCompleted", "unknown")
        limit = context.config_properties.get(
            "hdds.scm.block.deletion.per-interval.max", "unknown"
        )
        return (
            "Ozone SCM block deletion backlog not draining. "
            f"numBlockDeletionTransactions={pending} "
            f"numBlockDeletionTransactionCompleted={completed} "
            f"hdds.scm.block.deletion.per-interval.max={limit}. "
            "DeletedBlockLog, datanode deletion command acks, SCM block "
            "deleting service interval."
        )

    def permitted_actions(self) -> List[ActionSpec]:
        logger.info("SCM plugin: permitted_actions -> %s", INCREASE_SCM_BLOCK_DELETION_LIMIT)
        return [
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
        ]

    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        if action_id != INCREASE_SCM_BLOCK_DELETION_LIMIT:
            raise ValueError(
                f"action_id={action_id!r} is not valid for the SCM deletion plugin. "
                f"Expected {INCREASE_SCM_BLOCK_DELETION_LIMIT!r}."
            )

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
