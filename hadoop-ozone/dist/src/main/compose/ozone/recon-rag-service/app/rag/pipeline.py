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
import re

from app.models import DiagnosticContext, DiagnosisResponse, RecommendedFix
from app.plugins.base import AlertDiagnosticPlugin
from app.rag import prompt_templates
from app.rag.llm_client import LLMClient, get_llm_client
from app.rag.vector_store import VectorStore, get_vector_store


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def _parse_llm_json(raw_reply: str) -> dict:
    """Parse JSON from an LLM reply, tolerating optional markdown fences."""

    text = raw_reply.strip()
    match = _FENCED_JSON_RE.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


class RagPipeline:

    def __init__(self, vector_store: VectorStore, llm_client: LLMClient) -> None:
        self._vector_store = vector_store
        self._llm_client = llm_client

    def diagnose(
        self, context: DiagnosticContext, plugin: AlertDiagnosticPlugin
    ) -> DiagnosisResponse:
        alert_confirmed, verdict_reason = plugin.evaluate_alert(context)

        permitted_actions = plugin.permitted_actions_for(context)
        permitted_action_ids = {action.action_id for action in permitted_actions}

        query = plugin.retrieval_query(context)
        retrieved_documents = self._vector_store.query(query, top_k=3)

        prompt = prompt_templates.render(
            context, retrieved_documents, permitted_actions, alert_confirmed, verdict_reason
        )
        raw_reply = self._llm_client.generate(prompt)

        try:
            parsed = _parse_llm_json(raw_reply)
        except ValueError:
            parse_error = (
                "The diagnosis backend returned a response that could not be "
                "parsed as JSON. Raw response has been included in evidence "
                "for troubleshooting."
            )
            return DiagnosisResponse(
                alert_type=context.alert.alert_type,
                alert_confirmed=alert_confirmed,
                verdict_reason=verdict_reason,
                what_happened=parse_error,
                why_it_happened="",
                how_to_fix="Retry diagnosis after verifying recon-rag-service logs.",
                diagnosis=parse_error,
                evidence=[raw_reply],
                recommended_fix=None,
                retrieved_documents=retrieved_documents,
            )

        recommended_fix = None
        fix_payload = parsed.get("recommended_fix") if alert_confirmed else None
        if not alert_confirmed and parsed.get("recommended_fix"):
            parsed.setdefault("evidence", []).append(
                "A recommended fix was suppressed because the alert could not be "
                "confirmed against current metrics (see verdict above)."
            )
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

        what_happened = parsed.get("what_happened") or parsed.get("diagnosis", "")
        why_it_happened = parsed.get("why_it_happened", "")
        how_to_fix = parsed.get("how_to_fix", "")
        if not how_to_fix and recommended_fix:
            config_lines = ", ".join(
                f"{key}={value}" for key, value in recommended_fix.config_changes.items()
            )
            how_to_fix = recommended_fix.summary
            if config_lines:
                how_to_fix = f"{how_to_fix} ({config_lines})"
            if recommended_fix.rationale:
                how_to_fix = f"{how_to_fix} {recommended_fix.rationale}"

        legacy_diagnosis = parsed.get("diagnosis") or what_happened
        evidence = [verdict_reason] + list(parsed.get("evidence", []))

        return DiagnosisResponse(
            alert_type=context.alert.alert_type,
            alert_confirmed=alert_confirmed,
            verdict_reason=verdict_reason,
            what_happened=what_happened,
            why_it_happened=why_it_happened,
            how_to_fix=how_to_fix,
            diagnosis=legacy_diagnosis,
            evidence=evidence,
            recommended_fix=recommended_fix,
            retrieved_documents=retrieved_documents,
        )


_pipeline: RagPipeline | None = None


def get_pipeline() -> RagPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RagPipeline(get_vector_store(), get_llm_client())
    return _pipeline
