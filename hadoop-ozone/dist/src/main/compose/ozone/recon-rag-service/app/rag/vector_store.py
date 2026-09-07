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
"""Pluggable retrieval backend for the RAG pipeline.

Two implementations behind the same interface:
  * InMemoryVectorStore (default): zero extra runtime dependency, indexes the
    handful of knowledge_base/*.md files with the hashing-trick embedding in
    embeddings.py. Good enough to rank a small, curated knowledge base.
  * ChromaVectorStore (opt-in via RAG_VECTOR_STORE_BACKEND=chroma): same
    interface, backed by a real chromadb collection, for anyone who wants to
    index the full Ozone docs/runbook corpus. Imported lazily so the default
    image doesn't need chromadb installed.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from app.config import settings
from app.models import RetrievedDocument
from app.rag.embeddings import cosine_similarity, embed

_LEADING_LICENSE_COMMENT_RE = re.compile(r"^\s*<!---.*?-->\s*", re.DOTALL)


@dataclass
class KnowledgeDocument:
    source: str
    text: str


class VectorStore(ABC):

    @abstractmethod
    def add_documents(self, documents: Iterable[KnowledgeDocument]) -> None:
        """Index the given documents."""

    @abstractmethod
    def query(self, text: str, top_k: int = 3) -> List[RetrievedDocument]:
        """Return up to ``top_k`` documents most relevant to ``text``,
        highest score first."""


class InMemoryVectorStore(VectorStore):

    def __init__(self) -> None:
        self._documents: List[KnowledgeDocument] = []
        self._vectors: List[List[float]] = []

    def add_documents(self, documents: Iterable[KnowledgeDocument]) -> None:
        for document in documents:
            self._documents.append(document)
            self._vectors.append(embed(document.text))

    def query(self, text: str, top_k: int = 3) -> List[RetrievedDocument]:
        if not self._documents:
            return []
        query_vector = embed(text)
        scored = [
            (cosine_similarity(query_vector, vector), document)
            for vector, document in zip(self._vectors, self._documents)
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedDocument(source=document.source, snippet=document.text[:800], score=score)
            for score, document in scored[:top_k]
        ]


class ChromaVectorStore(VectorStore):
    """Optional real vector-database backend. Only imports chromadb when
    actually selected via RAG_VECTOR_STORE_BACKEND=chroma."""

    _COLLECTION_NAME = "ozone_rag_knowledge_base"

    def __init__(self) -> None:
        import chromadb  # noqa: PLC0415 (intentionally lazy/optional import)

        client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._collection = client.get_or_create_collection(self._COLLECTION_NAME)
        self._next_id = self._collection.count()

    def add_documents(self, documents: Iterable[KnowledgeDocument]) -> None:
        documents = list(documents)
        if not documents:
            return
        ids = [str(self._next_id + i) for i in range(len(documents))]
        self._next_id += len(documents)
        self._collection.add(
            ids=ids,
            documents=[document.text for document in documents],
            metadatas=[{"source": document.source} for document in documents],
        )

    def query(self, text: str, top_k: int = 3) -> List[RetrievedDocument]:
        if self._collection.count() == 0:
            return []
        result = self._collection.query(query_texts=[text], n_results=top_k)
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            RetrievedDocument(
                source=metadata.get("source", "unknown"),
                snippet=document[:800],
                # Chroma returns a distance (lower = closer); invert to a
                # score so callers don't need to know which metric is in use.
                score=1.0 / (1.0 + distance),
            )
            for document, metadata, distance in zip(documents, metadatas, distances)
        ]


def _load_knowledge_base(directory: Path) -> List[KnowledgeDocument]:
    if not directory.is_dir():
        return []
    return [
        KnowledgeDocument(
            source=path.name,
            text=_LEADING_LICENSE_COMMENT_RE.sub("", path.read_text(encoding="utf-8"), count=1),
        )
        for path in sorted(directory.glob("*.md"))
    ]


_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Process-wide singleton, built and populated with the knowledge base
    on first use."""

    global _vector_store
    if _vector_store is None:
        store: VectorStore
        if settings.vector_store_backend == "chroma":
            store = ChromaVectorStore()
        else:
            store = InMemoryVectorStore()
        store.add_documents(_load_knowledge_base(settings.knowledge_base_dir))
        _vector_store = store
    return _vector_store
