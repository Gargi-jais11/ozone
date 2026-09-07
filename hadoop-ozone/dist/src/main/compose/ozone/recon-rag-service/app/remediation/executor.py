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
"""Validates a requested remediation action and produces a plan.

This module never calls out to the cluster to change anything -- it only
ever asks a plugin to describe what a change would look like. Live
execution (dryRun=false) is rejected unless RAG_ALLOW_LIVE_REMEDIATION is
set, which no shipped configuration does; the flag exists purely as the
documented seam for the (currently unimplemented) future live-execution
path.
"""

from app.config import settings
from app.models import DiagnosticContext, RemediationPlan
from app.plugins.base import AlertDiagnosticPlugin


class ActionNotPermittedError(ValueError):
    """``action_id`` is not in the plugin's permitted_actions() allowlist."""


class LiveRemediationNotSupportedError(RuntimeError):
    """A caller asked for dryRun=false, which this build does not support."""


def validate_and_plan(
    plugin: AlertDiagnosticPlugin,
    action_id: str,
    context: DiagnosticContext,
    dry_run: bool,
) -> RemediationPlan:
    permitted_ids = {action.action_id for action in plugin.permitted_actions()}
    if action_id not in permitted_ids:
        raise ActionNotPermittedError(
            f"action_id={action_id!r} is not a permitted action for "
            f"alert_type={plugin.alert_type!r}. Permitted actions: {sorted(permitted_ids)}"
        )

    if not dry_run and not settings.allow_live_remediation:
        raise LiveRemediationNotSupportedError(
            "Live remediation (dryRun=false) is not implemented in this build. "
            "Only dry-run validation/planning is supported."
        )

    plan = plugin.build_remediation_plan(action_id, context)
    plan.dry_run = dry_run
    return plan
