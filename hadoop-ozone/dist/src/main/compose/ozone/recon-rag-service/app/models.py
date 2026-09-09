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
"""Request/response schemas shared by the plugins, the RAG pipeline and the API.

Kept in one module because every layer (plugin -> pipeline -> executor -> API)
passes the same handful of shapes back and forth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AlertPayload(BaseModel):
    """Shape of a single Prometheus alert, as sent by the Recon UI.

    Mirrors what Prometheus's ``/api/v1/alerts`` (and Alertmanager) return per
    alert, so the Recon UI can forward an alert object it already has
    unmodified.
    """

    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    state: Optional[str] = None
    activeAt: Optional[str] = None

    @property
    def alert_type(self) -> str:
        return self.labels.get("alertname", "")


class ActionSpec(BaseModel):
    """One remediation action a plugin is allowed to ever propose."""

    action_id: str
    description: str
    config_property: Optional[str] = None
    risk: str = "low"
    requires_restart: bool = False


class DiagnosticContext(BaseModel):
    """Everything a plugin gathered about the cluster for one alert."""

    alert: AlertPayload
    jmx_metrics: Dict[str, Any] = Field(default_factory=dict)
    config_properties: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class RetrievedDocument(BaseModel):
    source: str
    snippet: str
    score: float


class RecommendedFix(BaseModel):
    action_id: str
    summary: str
    config_changes: Dict[str, str] = Field(default_factory=dict)
    rationale: str


class DiagnosisResponse(BaseModel):
    alert_type: str
    alert_confirmed: bool = True
    verdict_reason: str = ""
    what_happened: str
    why_it_happened: str
    how_to_fix: str
    diagnosis: str = ""
    evidence: List[str] = Field(default_factory=list)
    recommended_fix: Optional[RecommendedFix] = None
    retrieved_documents: List[RetrievedDocument] = Field(default_factory=list)


class RemediationRequest(BaseModel):
    alert: AlertPayload
    action_id: str


class RemediationPlan(BaseModel):
    action_id: str
    description: str
    config_changes: Dict[str, str] = Field(default_factory=dict)
    requires_restart: bool = False
    risk: str = "low"
    dry_run: bool = True
    applied: bool = False
    warnings: List[str] = Field(default_factory=list)
