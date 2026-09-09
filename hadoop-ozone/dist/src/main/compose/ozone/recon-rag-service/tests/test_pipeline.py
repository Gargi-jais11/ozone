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

import json
from typing import Iterable, List

from app.models import AlertPayload, DiagnosticContext, RetrievedDocument
from app.plugins.deletion_not_progressing import (
    INCREASE_KEY_DELETING_LIMIT,
    DeletionNotProgressingPlugin,
)
from app.rag.llm_client import LLMClient
from app.rag.pipeline import RagPipeline
from app.rag.vector_store import KnowledgeDocument, VectorStore

ALERT = AlertPayload(
    labels={"alertname": "OzoneOmDeletionNotProgressing", "component": "om", "instance": "om:9874"}
)


class FakeVectorStore(VectorStore):

    def add_documents(self, documents: Iterable[KnowledgeDocument]) -> None:
        pass

    def query(self, text: str, top_k: int = 3) -> List[RetrievedDocument]:
        return [RetrievedDocument(source="fake.md", snippet="fake snippet", score=0.9)]


class FakeLLMClient(LLMClient):

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def generate(self, prompt: str) -> str:
        return self._reply


def _context(**config_properties: str) -> DiagnosticContext:
    return DiagnosticContext(
        alert=ALERT,
        jmx_metrics={"NumKeysProcessed": 10, "NumKeysPurged": 1},
        config_properties=config_properties,
        notes=[],
    )


def test_diagnose_parses_valid_json_and_keeps_permitted_fix():
    reply = json.dumps(
        {
            "what_happened": "Key deletion backlog is growing.",
            "why_it_happened": "Scan limit is too low.",
            "how_to_fix": "Double ozone.key.deleting.limit.per.task.",
            "evidence": ["numKeysProcessed=10"],
            "recommended_fix": {
                "action_id": INCREASE_KEY_DELETING_LIMIT,
                "summary": "double the limit",
                "config_changes": {"ozone.key.deleting.limit.per.task": "100000"},
                "rationale": "because",
            },
        }
    )
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient(reply))

    response = pipeline.diagnose(
        _context(**{"ozone.key.deleting.limit.per.task": "50000"}),
        DeletionNotProgressingPlugin(),
    )

    assert response.what_happened == "Key deletion backlog is growing."
    assert response.why_it_happened == "Scan limit is too low."
    assert response.how_to_fix == "Double ozone.key.deleting.limit.per.task."
    assert response.recommended_fix.action_id == INCREASE_KEY_DELETING_LIMIT
    assert response.retrieved_documents[0].source == "fake.md"


def test_diagnose_drops_unpermitted_action_id():
    reply = json.dumps(
        {
            "what_happened": "x",
            "why_it_happened": "y",
            "how_to_fix": "z",
            "evidence": [],
            "recommended_fix": {
                "action_id": "delete_all_the_data",
                "summary": "nope",
                "config_changes": {},
                "rationale": "nope",
            },
        }
    )
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient(reply))

    response = pipeline.diagnose(_context(), DeletionNotProgressingPlugin())

    assert response.recommended_fix is None
    assert any("unpermitted" in note for note in response.evidence)


def test_diagnose_falls_back_to_legacy_diagnosis_field():
    reply = json.dumps(
        {
            "diagnosis": "legacy combined summary",
            "evidence": ["note"],
            "recommended_fix": {
                "action_id": INCREASE_KEY_DELETING_LIMIT,
                "summary": "double the limit",
                "config_changes": {"ozone.key.deleting.limit.per.task": "100000"},
                "rationale": "because backlog",
            },
        }
    )
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient(reply))

    response = pipeline.diagnose(_context(), DeletionNotProgressingPlugin())

    assert response.what_happened == "legacy combined summary"
    assert response.how_to_fix.startswith("double the limit")


def test_diagnose_parses_json_wrapped_in_markdown_fences():
    reply = """```json
{
  "what_happened": "OM deletion stalled",
  "why_it_happened": "limit too low",
  "how_to_fix": "increase limit",
  "evidence": [],
  "recommended_fix": {
    "action_id": "increase_key_deleting_limit_per_task",
    "summary": "double the limit",
    "config_changes": {"ozone.key.deleting.limit.per.task": "100000"},
    "rationale": "because"
  }
}
```"""
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient(reply))

    response = pipeline.diagnose(_context(), DeletionNotProgressingPlugin())

    assert response.what_happened == "OM deletion stalled"
    assert response.recommended_fix.action_id == INCREASE_KEY_DELETING_LIMIT


def test_diagnose_handles_malformed_json_reply():
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient("not json at all"))

    response = pipeline.diagnose(_context(), DeletionNotProgressingPlugin())

    assert "could not be parsed" in response.what_happened.lower()
    assert response.recommended_fix is None


def test_diagnose_suppresses_fix_when_alert_not_confirmed():
    reply = json.dumps(
        {
            "what_happened": "backlog too large",
            "why_it_happened": "scan limit too low",
            "how_to_fix": "double the limit",
            "evidence": [],
            "recommended_fix": {
                "action_id": INCREASE_KEY_DELETING_LIMIT,
                "summary": "double the limit",
                "config_changes": {"ozone.key.deleting.limit.per.task": "100000"},
                "rationale": "because",
            },
        }
    )
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient(reply))

    no_backlog_context = DiagnosticContext(
        alert=ALERT,
        jmx_metrics={"NumKeysProcessed": 10, "NumKeysPurged": 10},
        config_properties={},
        notes=[],
    )
    response = pipeline.diagnose(no_backlog_context, DeletionNotProgressingPlugin())

    assert response.alert_confirmed is False
    assert response.recommended_fix is None
    assert any("suppressed" in note for note in response.evidence)
