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

ALERT = AlertPayload(labels={"alertname": "OzoneDeletionNotProgressing"})


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
        jmx_metrics={"numKeysProcessed": 10, "numKeysPurged": 1},
        config_properties=config_properties,
        notes=[],
    )


def test_diagnose_parses_valid_json_and_keeps_permitted_fix():
    reply = json.dumps(
        {
            "diagnosis": "backlog too large",
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

    assert response.diagnosis == "backlog too large"
    assert response.recommended_fix.action_id == INCREASE_KEY_DELETING_LIMIT
    assert response.retrieved_documents[0].source == "fake.md"


def test_diagnose_drops_unpermitted_action_id():
    reply = json.dumps(
        {
            "diagnosis": "x",
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


def test_diagnose_handles_malformed_json_reply():
    pipeline = RagPipeline(FakeVectorStore(), FakeLLMClient("not json at all"))

    response = pipeline.diagnose(_context(), DeletionNotProgressingPlugin())

    assert "could not be parsed" in response.diagnosis.lower()
    assert response.recommended_fix is None
