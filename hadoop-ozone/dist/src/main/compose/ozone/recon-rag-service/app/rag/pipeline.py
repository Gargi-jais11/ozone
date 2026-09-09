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
import logging
import re
from typing import Optional

from app.models import DiagnosticContext, DiagnosisResponse, RecommendedFix
from app.plugins.base import AlertDiagnosticPlugin
from app.rag import prompt_templates
from app.rag.llm_client import LLMClient, get_llm_client
from app.rag.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)


def _validate_shape(parsed: object) -> Optional[str]:
    """Reject a syntactically-valid JSON reply that doesn't match the schema
    the prompt asked for, before any of its fields are trusted."""

    if not isinstance(parsed, dict):
        return "top-level JSON value is not an object"
    has_diagnosis = isinstance(parsed.get("diagnosis"), str) and bool(parsed["diagnosis"])
    has_what_happened = isinstance(parsed.get("what_happened"), str) and bool(parsed["what_happened"])
    if not has_diagnosis and not has_what_happened:
        return "missing or non-string 'diagnosis'/'what_happened' field"
    if not isinstance(parsed.get("evidence", []), list):
        return "'evidence' field is not a list"
    fix = parsed.get("recommended_fix")
    if fix is not None and not isinstance(fix, dict):
        return "'recommended_fix' is present but not an object"
    return None


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
        logger.info(
            "diagnose alert_type=%s: jmx_metrics=%s config_properties=%s notes=%s permitted_action_ids=%s",
            context.alert.alert_type, context.jmx_metrics, context.config_properties,
            context.notes, sorted(permitted_action_ids),
        )

        query = plugin.retrieval_query(context)
        retrieved_documents = self._vector_store.query(query, top_k=3)
        logger.info(
            "diagnose alert_type=%s: retrieval_query=%r retrieved_documents=%s",
            context.alert.alert_type, query, retrieved_documents,
        )

        prompt = prompt_templates.render(
            context, retrieved_documents, permitted_actions, alert_confirmed, verdict_reason
        )
        logger.debug("diagnose alert_type=%s: prompt=%s", context.alert.alert_type, prompt)
        raw_reply = self._llm_client.generate(prompt)
        logger.info("diagnose alert_type=%s: raw_reply=%s", context.alert.alert_type, raw_reply)

        try:
            parsed = _parse_llm_json(raw_reply)
        except ValueError as exc:
            logger.warning(
                "diagnose alert_type=%s: raw_reply is not valid JSON: %s", context.alert.alert_type, exc
            )
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

        shape_error = _validate_shape(parsed)
        if shape_error:
            logger.warning(
                "diagnose alert_type=%s: raw_reply failed schema validation: %s",
                context.alert.alert_type, shape_error,
            )
            schema_error = (
                "The diagnosis backend returned a response with an invalid "
                f"schema ({shape_error}). Raw response has been included in "
                "evidence for troubleshooting."
            )
            return DiagnosisResponse(
                alert_type=context.alert.alert_type,
                alert_confirmed=alert_confirmed,
                verdict_reason=verdict_reason,
                what_happened=schema_error,
                why_it_happened="",
                how_to_fix="Retry diagnosis after verifying recon-rag-service logs.",
                diagnosis=schema_error,
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
                logger.warning(
                    "diagnose alert_type=%s: dropped unpermitted action_id=%r (permitted=%s)",
                    context.alert.alert_type, action_id, sorted(permitted_action_ids),
                )
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

        logger.info(
            "diagnose alert_type=%s: final what_happened=%r recommended_fix=%s",
            context.alert.alert_type, what_happened,
            recommended_fix.action_id if recommended_fix else None,
        )
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
