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
"""Dependency-free bag-of-words hashing-trick embedding, used by
InMemoryVectorStore so the default image needs no ML runtime at all.

Not meant to compete with a real sentence embedding model -- it's a
deterministic, swappable placeholder behind the same VectorStore interface a
real embedding model or Chroma's own embedding function would sit behind.
"""

import re
import zlib
from typing import List

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def embed(text: str, dims: int = 256) -> List[float]:
    vector = [0.0] * dims
    for token in _tokenize(text):
        bucket = zlib.crc32(token.encode("utf-8")) % dims
        vector[bucket] += 1.0

    norm = sum(component * component for component in vector) ** 0.5
    if norm == 0:
        return vector
    return [component / norm for component in vector]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
