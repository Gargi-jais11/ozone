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
"""Prompt construction for the diagnosis step.

The rendered prompt embeds a single fenced ```json context block``` carrying
every fact the plugin collected. Both LLMClient implementations only ever see
this one string: MockLLMClient parses the JSON block back out to reason
deterministically, and a real model reads the same block as grounding context
-- so the two behave as true drop-in replacements for each other.
"""

import json
from typing import List

from app.models import ActionSpec, DiagnosticContext, RetrievedDocument

SYSTEM_PROMPT = (
    "You are an Ozone site-reliability assistant. You diagnose cluster alerts "
    "using only the metrics, configuration and knowledge-base excerpts you are "
    "given, and you must never propose a remediation action_id that is not in "
    "the permitted_action_ids list. Respond with ONLY the JSON object described "
    "in the prompt, no surrounding prose."
)

_OUTPUT_SCHEMA = """{
  "diagnosis": "<one paragraph explaining the likely root cause>",
  "evidence": ["<short factual bullet>", "..."],
  "recommended_fix": {
    "action_id": "<one of permitted_action_ids, or omit the whole object if none apply>",
    "summary": "<one line>",
    "config_changes": {"<property>": "<proposed value>"},
    "rationale": "<why this fixes the diagnosed cause>"
  }
}"""


def render(
    context: DiagnosticContext,
    retrieved_documents: List[RetrievedDocument],
    permitted_actions: List[ActionSpec],
) -> str:
    context_block = {
        "alert_type": context.alert.alert_type,
        "alert_labels": context.alert.labels,
        "alert_annotations": context.alert.annotations,
        "jmx_metrics": context.jmx_metrics,
        "config_properties": context.config_properties,
        "notes": context.notes,
        "permitted_action_ids": [action.action_id for action in permitted_actions],
        "retrieved_sources": [document.source for document in retrieved_documents],
    }

    retrieved_text = "\n\n".join(
        f"### {document.source} (relevance={document.score:.2f})\n{document.snippet}"
        for document in retrieved_documents
    ) or "(no matching knowledge-base excerpts found)"

    return (
        f"{SYSTEM_PROMPT}\n\n"
        "## Collected context\n"
        "```json\n"
        f"{json.dumps(context_block, indent=2, default=str)}\n"
        "```\n\n"
        "## Retrieved knowledge-base excerpts\n"
        f"{retrieved_text}\n\n"
        "## Required output schema\n"
        f"{_OUTPUT_SCHEMA}\n"
    )
