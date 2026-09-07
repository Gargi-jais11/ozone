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
"""Sample plugin: diagnoses the ``OzoneDeletionNotProgressing`` alert.

Collects the OM key-deletion metrics (org.apache.hadoop.ozone.om.
DeletingServiceMetrics, JMX bean Hadoop:service=OzoneManager,
name=DeletingServiceMetrics) and the deletion-related tunables from
ozone-site.xml, then hands both to the RAG pipeline for analysis.
"""

from typing import List

from app.collectors.config_client import ConfigFetchError, fetch_properties
from app.collectors.jmx_client import JmxFetchError, fetch_bean
from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan
from app.plugins.base import AlertDiagnosticPlugin
from app.plugins.registry import register_plugin

DELETING_SERVICE_JMX_QUERY = "Hadoop:service=OzoneManager,name=DeletingServiceMetrics"

RELEVANT_CONFIG_PROPERTIES = (
    "ozone.key.deleting.limit.per.task",
    "ozone.snapshot.key.deleting.limit.per.task",
    "ozone.block.deleting.service.interval",
    "ozone.block.deleting.container.limit.per.interval",
    "ozone.snapshot.deep.cleaning.enabled",
)

INCREASE_KEY_DELETING_LIMIT = "increase_key_deleting_limit_per_task"
DEFAULT_KEY_DELETING_LIMIT_PER_TASK = 50000


@register_plugin
class DeletionNotProgressingPlugin(AlertDiagnosticPlugin):

    @property
    def alert_type(self) -> str:
        return "OzoneDeletionNotProgressing"

    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        notes: List[str] = []
        jmx_metrics = {}
        try:
            bean = fetch_bean(cluster.om_http_address, qry=DELETING_SERVICE_JMX_QUERY)
            if bean:
                jmx_metrics = {
                    key: bean[key]
                    for key in (
                        "numKeysProcessed",
                        "numKeysSentForPurge",
                        "numKeysPurged",
                        "numDirsSentForPurge",
                        "numDirsPurged",
                        "metricsResetTimeStamp",
                    )
                    if key in bean
                }
            else:
                notes.append(
                    "DeletingServiceMetrics MBean was not found on the OM JMX "
                    "endpoint -- the key deletion service may not have started."
                )
        except JmxFetchError as exc:
            notes.append(f"Could not reach OM JMX endpoint: {exc}")

        config_properties = {}
        try:
            config_properties = fetch_properties(
                cluster.om_http_address, RELEVANT_CONFIG_PROPERTIES
            )
        except ConfigFetchError as exc:
            notes.append(f"Could not reach OM config endpoint: {exc}")

        return DiagnosticContext(
            alert=alert,
            jmx_metrics=jmx_metrics,
            config_properties=config_properties,
            notes=notes,
        )

    def retrieval_query(self, context: DiagnosticContext) -> str:
        processed = context.jmx_metrics.get("numKeysProcessed", "unknown")
        purged = context.jmx_metrics.get("numKeysPurged", "unknown")
        limit = context.config_properties.get("ozone.key.deleting.limit.per.task", "unknown")
        return (
            "Ozone OM key deletion service (KeyDeletingService) not progressing. "
            f"numKeysProcessed={processed} numKeysPurged={purged} "
            f"ozone.key.deleting.limit.per.task={limit}. "
            "Deep cleaning, snapshot chain, deleted table backlog, "
            "block deletion pipeline."
        )

    def permitted_actions(self) -> List[ActionSpec]:
        return [
            ActionSpec(
                action_id=INCREASE_KEY_DELETING_LIMIT,
                description=(
                    "Double ozone.key.deleting.limit.per.task so KeyDeletingService "
                    "scans more keys per run. Only helps when the service is running "
                    "but under-provisioned, not when it is stuck or blocked."
                ),
                config_property="ozone.key.deleting.limit.per.task",
                risk="medium",
                requires_restart=True,
            ),
        ]

    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        if action_id != INCREASE_KEY_DELETING_LIMIT:
            raise ValueError(f"Unsupported action_id for this plugin: {action_id!r}")

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
            action_id=action_id,
            description=(
                f"Set ozone.key.deleting.limit.per.task from {current_limit} to "
                f"{proposed_limit} on the Ozone Manager(s)."
            ),
            config_changes={
                "ozone.key.deleting.limit.per.task": str(proposed_limit),
            },
            requires_restart=True,
            risk="medium",
            dry_run=True,
            applied=False,
            warnings=warnings,
        )
