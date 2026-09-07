<!---
 Licensed to the Apache Software Foundation (ASF) under one or more
 contributor license agreements.  See the NOTICE file distributed with
 this work for additional information regarding copyright ownership.
 The ASF licenses this file to you under the Apache License, Version 2.0
 (the "License"); you may not use this file except in compliance with
 the License.  You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
-->

# Ozone Recon RAG Diagnosis Service (prototype)

A standalone FastAPI microservice that diagnoses Ozone cluster alerts using a
pluggable, retrieval-augmented pipeline, and proposes (dry-run only)
remediations. This is a personal prototype/POC, not an ASF contribution -- no
Jira ID or OEP is associated with it.

## Scope of this build

- **Diagnosis is real**: JMX and configuration are read live from the Ozone
  Manager via its `/jmx` and `/conf` servlets, ranked against a small
  knowledge base, and turned into a diagnosis by a pluggable "LLM" backend.
- **The RAG internals are a working skeleton with mocked intelligence**: the
  vector store and the LLM are both `ABC`s with a dependency-free,
  deterministic default implementation, swappable via env vars for a real
  vector DB / chat-completions endpoint. See "Swapping in real backends"
  below.
- **Remediation never mutates the cluster.** `POST /api/v1/remediate` only
  ever returns a plan describing what a fix *would* change. `dryRun=false`
  is rejected with HTTP 501 in this build.

## Architecture

```
 Prometheus (dev-cluster monitoring.yaml add-on)
      |  fires alert
      v
 Ozone Recon UI --GET /api/v1/metrics/alerts--> Recon backend --> Prometheus /api/v1/alerts
      |   (existing generic MetricsProxyEndpoint, no Recon backend change)
      |
      |   new "Alerts" page lists active alerts, "Diagnose"/"Fix" buttons per row
      |
      |   POST http://<host>:8642/api/v1/diagnose   (direct browser -> this
      v    container; published port, CORS-enabled)
 recon-rag-service (this container, port 8642)
      |
      +-- PluginRegistry: labels.alertname -> AlertDiagnosticPlugin
      +-- plugin.collect_context(): GET om:9874/jmx?qry=..., GET om:9874/conf
      +-- RagPipeline: VectorStore.query(retrieval_query) + LLMClient.generate(prompt)
      +-- DiagnosisResponse{ diagnosis, evidence, recommended_fix }
      |
      |   "Fix" button -> POST /api/v1/remediate?dryRun=true
      v
 RemediationExecutor: validates action_id against plugin.permitted_actions(),
 returns a RemediationPlan describing the change. Never calls the cluster.
```

## Pluggable architecture

`app/plugins/base.py` defines `AlertDiagnosticPlugin`, one implementation per
Prometheus `alertname`:

- `alert_type` -- the alertname this plugin handles.
- `collect_context(alert, cluster)` -- gather JMX metrics / config properties.
- `retrieval_query(context)` -- text used to search the knowledge base.
- `permitted_actions()` -- fixed allowlist of remediation actions this plugin
  may ever propose; the executor rejects anything else regardless of what the
  LLM suggests.
- `build_remediation_plan(action_id, context)` -- describes a change without
  applying it.

New plugins register themselves with `@register_plugin` at import time
(`app/plugins/registry.py`) -- the Python analogue of the `ServiceLoader`/SPI
pattern already used in Ozone (e.g. `OmTransportFactory.createFactory`).
Adding a second alert type means adding a new module, not editing a
dispatcher.

`app/plugins/deletion_not_progressing.py` is the one shipped plugin, for the
`OzoneDeletionNotProgressing` alert: it reads the OM's
`DeletingServiceMetrics` JMX bean and the relevant `ozone.*.deleting.*`
config properties, and proposes doubling
`ozone.key.deleting.limit.per.task` as its only permitted action.

## RAG pipeline strategy

`app/rag/pipeline.py`'s `RagPipeline.diagnose()`:

1. Calls `plugin.retrieval_query(context)` and looks up the top 3 matching
   knowledge-base chunks via a `VectorStore`.
2. Renders a single prompt (`app/rag/prompt_templates.py`) containing a
   machine-parseable JSON block (the collected JMX/config/notes) plus the
   retrieved excerpts, and asks for a JSON-shaped diagnosis.
3. Sends that prompt to an `LLMClient` and parses the JSON reply into a
   `DiagnosisResponse`, dropping (with a warning) any `recommended_fix`
   whose `action_id` isn't in the plugin's permitted-actions allowlist.

### Swapping in real backends

Both interfaces are selected purely from environment variables, so the same
pipeline code runs whether or not real backends are configured:

| Component | Default | Real backend |
|---|---|---|
| `VectorStore` | `InMemoryVectorStore`: dependency-free hashing-trick bag-of-words cosine similarity over `knowledge_base/*.md` | `RAG_VECTOR_STORE_BACKEND=chroma` -> `ChromaVectorStore` (requires `chromadb` installed; persists to `RAG_CHROMA_PERSIST_DIR`) |
| `LLMClient` | `MockLLMClient`: deterministic, rule-based JSON diagnosis generated from the same context block a real model would see | Set `RAG_LLM_BASE_URL` and `RAG_LLM_API_KEY` -> `OpenAICompatibleLLMClient` (any OpenAI chat-completions-compatible endpoint; model via `RAG_LLM_MODEL`) |

`MockLLMClient` is not a placeholder that returns canned text -- it extracts
the real JMX/config data embedded in the prompt and reasons over it, so the
full pipeline (retrieval, prompt construction, JSON-schema parsing, allowlist
enforcement) is exercised end-to-end without any external dependency.

## API

See [`openapi/rag-service.openapi.yaml`](openapi/rag-service.openapi.yaml) for
the full contract. Summary:

- `POST /api/v1/diagnose` -- body is a Prometheus alert object -> `DiagnosisResponse`.
- `POST /api/v1/remediate?dryRun=true` -- body `{alert, action_id}` -> `RemediationPlan`. `dryRun=false` -> 501.
- `GET /api/v1/plugins` -- registered alert types.
- `GET /api/v1/health`.

## Running locally

```
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload --port 8642
```

Environment variables of note (all optional, see `app/config.py` for the
full list and defaults): `OM_HTTP_ADDRESS`, `SCM_HTTP_ADDRESS`,
`RECON_HTTP_ADDRESS`, `RAG_VECTOR_STORE_BACKEND`, `RAG_LLM_BASE_URL`,
`RAG_LLM_API_KEY`, `RAG_CORS_ALLOWED_ORIGINS`.

## Running with the Ozone compose cluster

See the `rag-service.yaml` add-on and its section in the compose
[`README.md`](../README.md).

## Implementation roadmap

- [x] Phase 1: plugin ABC, registry, and the `DeletionNotProgressing` plugin
      with live JMX/config collection.
- [x] Phase 2: RAG pipeline with swappable vector store / LLM, mock defaults.
- [x] Phase 3: dry-run-only remediation executor and REST API.
- [x] Phase 4: docker-compose add-on wiring and a minimal Recon UI page.
- [ ] Deferred: live remediation execution (`dryRun=false`) against a real
      cluster, gated behind explicit operator opt-in and an audit trail.
- [ ] Deferred: additional plugins beyond `DeletionNotProgressing` (the
      ABC/registry already generalize to this).
- [ ] Deferred: a curated, larger knowledge base beyond the two illustrative
      runbook documents shipped here.
- [ ] Deferred: server-side configuration of the RAG service base URL for the
      Recon UI (currently a frontend constant, fine for a local prototype).
