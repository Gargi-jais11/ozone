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

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models import AlertPayload, DiagnosisResponse, RemediationPlan, RemediationRequest
from app.plugins.registry import get_plugin, list_plugins
from app.rag.pipeline import get_pipeline
from app.remediation.executor import (
    ActionNotPermittedError,
    LiveRemediationNotSupportedError,
    validate_and_plan,
)

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
    try:
        plugin = get_plugin(alert_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    context = plugin.collect_context(alert, settings.cluster)
    return get_pipeline().diagnose(context, plugin)


@router.post("/remediate", response_model=RemediationPlan)
def remediate(
    request: RemediationRequest,
    dryRun: bool = Query(True, description="Must be true; live execution is not implemented."),
) -> RemediationPlan:
    try:
        plugin = get_plugin(request.alert.alert_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    context = plugin.collect_context(request.alert, settings.cluster)

    try:
        return validate_and_plan(plugin, request.action_id, context, dry_run=dryRun)
    except ActionNotPermittedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LiveRemediationNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
