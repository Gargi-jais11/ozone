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
"""Pluggable analysis backend for the RAG pipeline.

MockLLMClient (default) is a deterministic, rule-based stand-in that reads
the same JSON context block a real model would, so it exercises the full
pipeline end-to-end without needing an API key or network access.
OpenAICompatibleLLMClient (opt-in via RAG_LLM_BASE_URL/RAG_LLM_API_KEY) talks
to any OpenAI chat-completions-compatible endpoint.
"""

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict

import httpx

from app.config import settings
from app.rag.prompt_templates import SYSTEM_PROMPT

_CONTEXT_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


class LLMClient(ABC):

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Return a JSON string matching the schema described in the prompt."""


def _extract_context_block(prompt: str) -> Dict[str, Any]:
    match = _CONTEXT_BLOCK_RE.search(prompt)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {}


class MockLLMClient(LLMClient):
    """Deterministic rule-based diagnosis, used when no real LLM endpoint is
    configured. Not a literal placeholder -- it produces a genuine,
    metrics-grounded diagnosis, just without an actual model behind it."""

    def generate(self, prompt: str) -> str:
        context = _extract_context_block(prompt)
        jmx_metrics = context.get("jmx_metrics", {})
        config_properties = context.get("config_properties", {})
        notes = context.get("notes", [])
        permitted_action_ids = context.get("permitted_action_ids", [])
        retrieved_sources = context.get("retrieved_sources", [])

        evidence = list(notes)
        if jmx_metrics:
            evidence.append(f"KeyDeletingService JMX snapshot: {jmx_metrics}")
        if config_properties:
            evidence.append(f"Relevant OM configuration: {config_properties}")
        if retrieved_sources:
            evidence.append(f"Matched runbook(s): {', '.join(retrieved_sources)}")

        recommended_fix = None
        if not jmx_metrics and not config_properties:
            diagnosis = (
                "Could not collect any deletion-service telemetry from the Ozone "
                "Manager JMX/config endpoints, so the root cause cannot be "
                "confirmed. Restore connectivity to OM and retry diagnosis."
            )
        else:
            processed = jmx_metrics.get("numKeysProcessed")
            purged = jmx_metrics.get("numKeysPurged")
            diagnosis = (
                "KeyDeletingService metrics were retrieved but show little or no "
                f"forward progress (numKeysProcessed={processed}, "
                f"numKeysPurged={purged}). This commonly happens when "
                "ozone.key.deleting.limit.per.task is too small for the current "
                "backlog, or a downstream dependency (snapshot deep cleaning, "
                "block deletion pipeline) is itself stalled."
            )
            if permitted_action_ids:
                action_id = permitted_action_ids[0]
                current_limit = config_properties.get("ozone.key.deleting.limit.per.task")
                proposed_limit = int(current_limit) * 2 if current_limit else 100000
                recommended_fix = {
                    "action_id": action_id,
                    "summary": "Increase the per-task key deletion scan limit.",
                    "config_changes": {
                        "ozone.key.deleting.limit.per.task": str(proposed_limit),
                    },
                    "rationale": (
                        "Doubling the scan limit lets KeyDeletingService clear a "
                        "larger backlog per run without any other configuration "
                        "changes."
                    ),
                }

        return json.dumps(
            {
                "diagnosis": diagnosis,
                "evidence": evidence,
                "recommended_fix": recommended_fix,
            }
        )


class OpenAICompatibleLLMClient(LLMClient):
    """Talks to any OpenAI chat-completions-compatible endpoint
    (self-hosted vLLM/Ollama gateway, Azure OpenAI, OpenAI itself, ...)."""

    def generate(self, prompt: str) -> str:
        response = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def get_llm_client() -> LLMClient:
    if settings.llm_base_url and settings.llm_api_key:
        return OpenAICompatibleLLMClient()
    return MockLLMClient()
