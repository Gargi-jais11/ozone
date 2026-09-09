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
"""The pluggable contract every alert-specific diagnostic handler implements.

One plugin == one Prometheus alertname. The RAG pipeline and the remediation
executor only ever talk to plugins through this interface, so adding a new
alert type never requires touching the pipeline, the executor or the API
layer.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple

from app.config import ClusterEndpoints
from app.models import ActionSpec, AlertPayload, DiagnosticContext, RemediationPlan


class AlertDiagnosticPlugin(ABC):
    """Defines what to collect, what to retrieve, and what fixes are allowed
    for one alert type."""

    @property
    @abstractmethod
    def alert_type(self) -> str:
        """The Prometheus ``labels.alertname`` this plugin handles."""

    @abstractmethod
    def collect_context(
        self, alert: AlertPayload, cluster: ClusterEndpoints
    ) -> DiagnosticContext:
        """Gather JMX metrics, config properties and any other evidence
        needed to diagnose ``alert``."""

    @abstractmethod
    def retrieval_query(self, context: DiagnosticContext) -> str:
        """Free text used to look up relevant docs/runbooks in the vector
        store for ``context``."""

    def evaluate_alert(self, context: DiagnosticContext) -> Tuple[bool, str]:
        """Deterministically sanity-check the alert against the metrics/config
        just collected, independent of what the LLM concludes.

        Returns (confirmed, reason). ``confirmed=False`` means the collected
        evidence does not support the alert (e.g. a backlog that has already
        drained), so it may be stale or a false positive. The default
        implementation cannot judge without alert-specific knowledge, so it
        reports an inconclusive verdict; plugins should override this to
        apply real thresholds against their own metrics.
        """

        if not context.jmx_metrics and not context.config_properties:
            return False, (
                "No metrics or configuration could be collected, so the alert "
                "condition could not be independently verified."
            )
        return True, "Evidence was collected but this plugin does not implement a validity check."

    @abstractmethod
    def permitted_actions(self) -> List[ActionSpec]:
        """The fixed allowlist of remediation actions this plugin may ever
        propose. The remediation executor rejects any action_id not in this
        list, regardless of what an LLM suggests."""

    def permitted_actions_for(self, context: DiagnosticContext) -> List[ActionSpec]:
        """Actions that apply to this specific alert instance.

        Plugins with hop- or label-dependent remediations override this;
        the default returns the full allowlist.
        """

        return self.permitted_actions()

    @abstractmethod
    def build_remediation_plan(
        self, action_id: str, context: DiagnosticContext
    ) -> RemediationPlan:
        """Describe what ``action_id`` would change, without applying it.

        Implementations must not perform any cluster mutation here -- this
        method only produces a plan; whether/if it is ever executed is the
        remediation executor's decision.
        """
