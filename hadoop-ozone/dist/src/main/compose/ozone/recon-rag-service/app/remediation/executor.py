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
"""Validates a requested remediation action, produces a plan, and -- only
when explicitly asked for -- applies it to the cluster.

A plugin always describes what a change would look like
(build_remediation_plan) before anything is touched. When the caller passes
dryRun=false AND RAG_ALLOW_LIVE_REMEDIATION=true, validate_and_plan hands
that plan to app.remediation.live_apply, which edits the target container's
config and invokes Ozone's live `ozone admin reconfig`. Both gates exist so
a human has to explicitly opt this container into live execution
(RAG_ALLOW_LIVE_REMEDIATION) and explicitly confirm each apply
(dryRun=false) -- see rag-service.yaml and the Recon UI's Apply Fix flow.
"""

import logging

from app.config import settings
from app.models import DiagnosticContext, RemediationPlan
from app.plugins.base import AlertDiagnosticPlugin
from app.remediation import live_apply

logger = logging.getLogger(__name__)


class ActionNotPermittedError(ValueError):
    """``action_id`` is not in the plugin's permitted_actions() allowlist."""


class LiveRemediationNotSupportedError(RuntimeError):
    """A caller asked for dryRun=false while RAG_ALLOW_LIVE_REMEDIATION is
    not set on this container."""


class LiveRemediationExecutionError(RuntimeError):
    """A caller asked for dryRun=false, live remediation is enabled, but
    applying the plan to the cluster failed."""


def validate_and_plan(
    plugin: AlertDiagnosticPlugin,
    action_id: str,
    context: DiagnosticContext,
    dry_run: bool,
) -> RemediationPlan:
    permitted_ids = {
        action.action_id for action in plugin.permitted_actions_for(context)
    }
    logger.info(
        "validate_and_plan alert_type=%s: action_id=%s dry_run=%s permitted_ids=%s",
        plugin.alert_type, action_id, dry_run, sorted(permitted_ids),
    )
    if action_id not in permitted_ids:
        logger.warning(
            "validate_and_plan alert_type=%s: action_id=%s not permitted (permitted_ids=%s)",
            plugin.alert_type, action_id, sorted(permitted_ids),
        )
        raise ActionNotPermittedError(
            f"action_id={action_id!r} is not a permitted action for "
            f"alert_type={plugin.alert_type!r}. Permitted actions: {sorted(permitted_ids)}"
        )

    if not dry_run and not settings.allow_live_remediation:
        logger.warning(
            "validate_and_plan alert_type=%s: rejected dry_run=false (allow_live_remediation=%s)",
            plugin.alert_type, settings.allow_live_remediation,
        )
        raise LiveRemediationNotSupportedError(
            "Live remediation (dryRun=false) is not implemented in this build. "
            "Only dry-run validation/planning is supported."
        )

    try:
        plan = plugin.build_remediation_plan(action_id, context)
    except ValueError as exc:
        logger.warning(
            "validate_and_plan alert_type=%s: build_remediation_plan rejected action_id=%s: %s",
            plugin.alert_type, action_id, exc,
        )
        raise ActionNotPermittedError(str(exc)) from exc
    plan.dry_run = dry_run
    logger.info(
        "validate_and_plan alert_type=%s: plan config_changes=%s dry_run=%s",
        plugin.alert_type, plan.config_changes, plan.dry_run,
    )

    if not dry_run:
        try:
            plan.execution_log = live_apply.apply_plan(context, plan)
            plan.applied = True
            logger.info(
                "validate_and_plan alert_type=%s: applied action_id=%s execution_log=%s",
                plugin.alert_type, action_id, plan.execution_log,
            )
        except live_apply.LiveApplyError as exc:
            logger.error(
                "validate_and_plan alert_type=%s: failed to apply action_id=%s: %s",
                plugin.alert_type, action_id, exc,
            )
            raise LiveRemediationExecutionError(str(exc)) from exc

    return plan
