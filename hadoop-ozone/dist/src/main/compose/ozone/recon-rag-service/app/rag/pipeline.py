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
"""Ties retrieval and generation together into a single diagnose() call."""

import json

from app.models import DiagnosticContext, DiagnosisResponse, RecommendedFix
from app.plugins.base import AlertDiagnosticPlugin
from app.rag import prompt_templates
from app.rag.llm_client import LLMClient, get_llm_client
from app.rag.vector_store import VectorStore, get_vector_store


class RagPipeline:

    def __init__(self, vector_store: VectorStore, llm_client: LLMClient) -> None:
        self._vector_store = vector_store
        self._llm_client = llm_client

    def diagnose(
        self, context: DiagnosticContext, plugin: AlertDiagnosticPlugin
    ) -> DiagnosisResponse:
        permitted_actions = plugin.permitted_actions()
        permitted_action_ids = {action.action_id for action in permitted_actions}

        query = plugin.retrieval_query(context)
        retrieved_documents = self._vector_store.query(query, top_k=3)

        prompt = prompt_templates.render(context, retrieved_documents, permitted_actions)
        raw_reply = self._llm_client.generate(prompt)

        try:
            parsed = json.loads(raw_reply)
        except ValueError:
            return DiagnosisResponse(
                alert_type=context.alert.alert_type,
                diagnosis=(
                    "The diagnosis backend returned a response that could not be "
                    "parsed as JSON. Raw response has been included in evidence "
                    "for troubleshooting."
                ),
                evidence=[raw_reply],
                recommended_fix=None,
                retrieved_documents=retrieved_documents,
            )

        recommended_fix = None
        fix_payload = parsed.get("recommended_fix")
        if fix_payload:
            action_id = fix_payload.get("action_id")
            if action_id in permitted_action_ids:
                recommended_fix = RecommendedFix(
                    action_id=action_id,
                    summary=fix_payload.get("summary", ""),
                    config_changes=fix_payload.get("config_changes", {}),
                    rationale=fix_payload.get("rationale", ""),
                )
            else:
                parsed.setdefault("evidence", []).append(
                    f"Diagnosis backend proposed unrecognized/unpermitted "
                    f"action_id={action_id!r}; it was dropped."
                )

        return DiagnosisResponse(
            alert_type=context.alert.alert_type,
            diagnosis=parsed.get("diagnosis", ""),
            evidence=parsed.get("evidence", []),
            recommended_fix=recommended_fix,
            retrieved_documents=retrieved_documents,
        )


_pipeline: RagPipeline | None = None


def get_pipeline() -> RagPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RagPipeline(get_vector_store(), get_llm_client())
    return _pipeline
