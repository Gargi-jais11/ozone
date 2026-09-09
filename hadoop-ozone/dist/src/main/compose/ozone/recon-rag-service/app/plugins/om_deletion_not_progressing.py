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
"""Plugin for the OM key-deletion-stuck alert.

Prometheus alertname: ``OzoneOmDeletionNotProgressing``. Severity tiers
(low/medium/high/critical) come from how long the condition has held --
see ozone-aiops-alerts.yml.
"""

import logging
from typing import List, Optional, Tuple

from app.collectors.endpoints import resolve_http_address
from app.collectors.recon_client import ReconFetchError, fetch_deleted_key_summary
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins._deletion_common import fetch_config_properties, fetch_jmx_metrics
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.registry import register_plugin

logger = logging.getLogger(__name__)

OM_DELETING_SERVICE_JMX_QUERY = "Hadoop:service=OzoneManager,name=DeletingServiceMetrics"

INCREASE_KEY_DELETING_LIMIT = "increase_key_deleting_limit_per_task"
DEFAULT_KEY_DELETING_LIMIT_PER_TASK = 50000

OM_CONFIG_PROPERTIES = (
    "ozone.key.deleting.limit.per.task",
    "ozone.snapshot.key.deleting.limit.per.task",
    "ozone.block.deleting.service.interval",
    "ozone.block.deleting.container.limit.per.interval",
    "ozone.snapshot.deep.cleaning.enabled",
)

OM_JMX_KEYS = (
    "numKeysProcessed",
    "numKeysSentForPurge",
    "numKeysPurged",
    "numDirsSentForPurge",
    "numDirsPurged",
    "metricsResetTimeStamp",
)

# Not a JMX key: populated from Recon's live deletedTable count (see
# _fetch_recon_pending_delete_keys), not from the OM JMX endpoint. Stored
# alongside OM_JMX_KEYS in jmx_metrics since that is the one field the RAG
# prompt renders as collected telemetry.
RECON_PENDING_DELETE_KEYS_METRIC = "reconPendingDeleteKeys"


def _current_limit(config_properties: dict) -> int:
    value = config_properties.get("ozone.key.deleting.limit.per.task")
    return int(value) if value else DEFAULT_KEY_DELETING_LIMIT_PER_TASK


def _backlog_exceeds_limit(context: DiagnosticContext) -> bool:
    """True only when Recon's live delete-pending key count is actually
    greater than the configured per-task scan limit -- i.e. there is direct
    evidence a single KeyDeletingService run cannot drain the backlog."""

    pending = context.jmx_metrics.get(RECON_PENDING_DELETE_KEYS_METRIC)
    if not isinstance(pending, (int, float)):
        logger.info(
            "_backlog_exceeds_limit: no usable %s value (got %r) -- treating as no backlog",
            RECON_PENDING_DELETE_KEYS_METRIC, pending,
        )
        return False
    limit = _current_limit(context.config_properties)
    exceeds = pending > limit
    logger.info(
        "_backlog_exceeds_limit: pending=%s limit=%s -> exceeds=%s", pending, limit, exceeds
    )
    return exceeds


@register_plugin()
class OmDeletionNotProgressingPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneOmDeletionNotProgressing"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        http_address, endpoint_note = resolve_http_address(alert, cluster)
        notes: List[str] = []
        if endpoint_note:
            notes.append(endpoint_note)
        notes.append("Deletion hop under diagnosis: om")

        if not http_address:
            notes.append("No HTTP endpoint available for evidence collection.")
            return DiagnosticContext(alert=alert, notes=notes)

        jmx_metrics = fetch_jmx_metrics(
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
        config_properties = fetch_config_properties(
            http_address, OM_CONFIG_PROPERTIES, notes, "OM"
        )

        pending_keys = self._fetch_recon_pending_delete_keys(cluster.recon_http_address, notes)
        if pending_keys is not None:
            jmx_metrics[RECON_PENDING_DELETE_KEYS_METRIC] = pending_keys
            limit = _current_limit(config_properties)
            if pending_keys <= limit:
                notes.append(
                    f"Recon reports {pending_keys} keys currently pending deletion, at or "
                    f"below the configured ozone.key.deleting.limit.per.task ({limit}) -- "
                    "no evidence of a scan-limit-caused backlog."
                )

        return DiagnosticContext(
            alert=alert, jmx_metrics=jmx_metrics, config_properties=config_properties, notes=notes
        )

    @staticmethod
    def _fetch_recon_pending_delete_keys(
        recon_http_address: str, notes: List[str]
    ) -> Optional[int]:
        if not recon_http_address:
            logger.warning("No Recon endpoint configured; skipping live backlog check.")
            notes.append(
                "No Recon endpoint configured; cannot independently confirm the "
                "current delete-pending key backlog."
            )
            return None
        try:
            summary = fetch_deleted_key_summary(recon_http_address)
        except ReconFetchError as exc:
            logger.warning("Recon backlog check failed: %s", exc)
            notes.append(
                f"Could not reach Recon to confirm the current delete-pending key "
                f"backlog: {exc}"
            )
            return None
        pending = summary.get("totalDeletedKeys")
        logger.info("Recon deletePending/summary -> totalDeletedKeys=%s (raw=%s)", pending, summary)
        return pending

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        metrics = context.jmx_metrics
        if not metrics:
            return False, (
                "Could not collect OM deletion-service JMX metrics, so the "
                "backlog claimed by the alert could not be independently verified."
            )

        processed = metrics.get("numKeysProcessed")
        purged = metrics.get("numKeysPurged")
        if isinstance(processed, (int, float)) and isinstance(purged, (int, float)):
            backlog = processed - purged
            if backlog <= 0:
                return False, (
                    f"numKeysProcessed ({processed}) is not ahead of numKeysPurged "
                    f"({purged}); OM has no outstanding key-deletion backlog right "
                    "now, so this alert may be stale or already resolved."
                )
            return True, (
                f"OM has {backlog} key(s) processed but not yet purged "
                f"(numKeysProcessed={processed}, numKeysPurged={purged}), "
                "confirming a backlog."
            )

        return True, (
            "OM JMX metrics were collected but did not include the specific "
            "counters needed to confirm backlog size; treating the alert as "
            "unverified-but-plausible."
        )

    def retrieval_query(self, context: DiagnosticContext) -> str:
        processed = context.jmx_metrics.get("numKeysProcessed", "unknown")
        purged = context.jmx_metrics.get("numKeysPurged", "unknown")
        limit = context.config_properties.get("ozone.key.deleting.limit.per.task", "unknown")
        pending = context.jmx_metrics.get(RECON_PENDING_DELETE_KEYS_METRIC, "unknown")
        return (
            "Ozone OM key deletion service (KeyDeletingService) not progressing. "
            f"numKeysProcessed={processed} numKeysPurged={purged} "
            f"ozone.key.deleting.limit.per.task={limit} "
            f"reconPendingDeleteKeys={pending}. "
            "Deep cleaning, snapshot chain, deleted table backlog, "
            "block deletion pipeline."
        )

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
        ]

    def permitted_actions_for(self, context: DiagnosticContext) -> List[ActionSpec]:
        """Only offer to raise the scan limit when Recon's live delete-pending
        key count actually exceeds it -- an alert firing on its own is not
        evidence that the configured limit is the bottleneck."""

        if _backlog_exceeds_limit(context):
            logger.info("OM plugin: backlog exceeds limit -- offering %s", INCREASE_KEY_DELETING_LIMIT)
            return self.permitted_actions()
        logger.info(
            "OM plugin: no evidence of a scan-limit-caused backlog -- no action offered"
        )
        return []

    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        if action_id != INCREASE_KEY_DELETING_LIMIT:
            raise ValueError(
                f"action_id={action_id!r} is not valid for the OM deletion plugin. "
                f"Expected {INCREASE_KEY_DELETING_LIMIT!r}."
            )

        current_limit = _current_limit(context.config_properties)
        proposed_limit = current_limit * 2
        warnings = list(context.notes)
        if "ozone.key.deleting.limit.per.task" not in context.config_properties:
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
