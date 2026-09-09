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
"""HTTP surface of the recon-rag-service: diagnose, remediate, plugins, health."""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models import AlertPayload, DiagnosisResponse, RemediationPlan, RemediationRequest
from app.plugins.registry import get_plugin, list_plugins
from app.rag.pipeline import get_pipeline
from app.remediation.executor import (
    ActionNotPermittedError,
    LiveRemediationExecutionError,
    LiveRemediationNotSupportedError,
    validate_and_plan,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/plugins")
def plugins() -> dict:
    return {"plugins": list_plugins()}


@router.post("/diagnose", response_model=DiagnosisResponse)
def diagnose(alert: AlertPayload) -> DiagnosisResponse:
    alert_type = alert.alert_type
    logger.info("POST /diagnose alert_type=%s labels=%s", alert_type, alert.labels)
    try:
        plugin = get_plugin(alert_type)
    except KeyError as exc:
        logger.warning("No plugin registered for alert_type=%s", alert_type)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    context = plugin.collect_context(alert, settings.cluster)
    logger.info(
        "collect_context for alert_type=%s -> jmx_metrics=%s config_properties=%s notes=%s",
        alert_type, context.jmx_metrics, context.config_properties, context.notes,
    )
    response = get_pipeline().diagnose(context, plugin)
    logger.info(
        "diagnose response for alert_type=%s: diagnosis=%r recommended_fix=%s",
        alert_type, response.diagnosis,
        response.recommended_fix.action_id if response.recommended_fix else None,
    )
    return response


@router.post("/remediate", response_model=RemediationPlan)
def remediate(
    request: RemediationRequest,
    dryRun: bool = Query(
        True,
        description=(
            "true: plan only. false: apply to the cluster -- requires "
            "RAG_ALLOW_LIVE_REMEDIATION=true on this container."
        ),
    ),
) -> RemediationPlan:
    alert_type = request.alert.alert_type
    logger.info(
        "POST /remediate alert_type=%s action_id=%s dryRun=%s",
        alert_type, request.action_id, dryRun,
    )
    try:
        plugin = get_plugin(alert_type)
    except KeyError as exc:
        logger.warning("No plugin registered for alert_type=%s", alert_type)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    context = plugin.collect_context(request.alert, settings.cluster)
    logger.info(
        "collect_context for alert_type=%s -> jmx_metrics=%s config_properties=%s notes=%s",
        alert_type, context.jmx_metrics, context.config_properties, context.notes,
    )

    try:
        plan = validate_and_plan(plugin, request.action_id, context, dry_run=dryRun)
    except ActionNotPermittedError as exc:
        logger.warning("remediate rejected for alert_type=%s: %s", alert_type, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LiveRemediationNotSupportedError as exc:
        logger.warning("remediate rejected for alert_type=%s: %s", alert_type, exc)
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except LiveRemediationExecutionError as exc:
        logger.error("remediate failed to apply for alert_type=%s: %s", alert_type, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "remediate plan for alert_type=%s: config_changes=%s applied=%s",
        alert_type, plan.config_changes, plan.applied,
    )
    return plan
