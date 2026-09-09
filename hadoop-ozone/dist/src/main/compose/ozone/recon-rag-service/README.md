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

## End-to-end alert flow (Alertmanager integration)

The compose cluster wires Prometheus, Alertmanager, Recon, and this service
together. **The browser only talks to Recon**; Recon calls this Python service
server-side.

```
 Prometheus (ozone-aiops-alerts.yml rules)
      |  evaluates metrics, fires alert
      v
 Alertmanager (alertmanager.yml)
      |  POST webhook (Alertmanager payload)
      v
 Recon  POST /api/v1/aiops/webhook
      |  upsert by fingerprint -> RocksDB (aiopsAlertsTable)
      |
 Recon UI  GET /api/v1/aiops/alerts
      |  lists persisted alerts (not a live Prometheus poll)
      |
      |  "Diagnose" -> POST /api/v1/aiops/alerts/{id}/diagnose
      v
 Recon (Java RagServiceClient)
      |  POST http://recon-rag:8642/api/v1/diagnose
      v
 recon-rag-service (this container, port 8642)
      |
      +-- PluginRegistry: labels.alertname -> AlertDiagnosticPlugin
      +-- plugin.collect_context(): GET om:9874/jmx?qry=..., GET om:9874/conf
      +-- RagPipeline: VectorStore.query(retrieval_query) + LLMClient.generate(prompt)
      +-- DiagnosisResponse{ diagnosis, evidence, recommended_fix }
      |
      |  "Fix" -> POST /api/v1/aiops/alerts/{id}/remediate?dryRun=true&actionId=...
      v
 Recon (Java RagServiceClient)
      |  POST http://recon-rag:8642/api/v1/remediate?dryRun=true
      v
 RemediationExecutor: validates action_id against plugin.permitted_actions(),
 returns a RemediationPlan describing the change. Never calls the cluster.(never applies to cluster)
```

### What changed vs. the original browser-direct prototype

| Layer | Before | Now |
|---|---|---|
| Alert source | Recon UI polled Prometheus `/api/v1/alerts` | Alertmanager webhook -> Recon `/api/v1/aiops/webhook` -> RocksDB |
| Alert listing | `GET /api/v1/metrics/alerts` (MetricsProxy) | `GET /api/v1/aiops/alerts` |
| Diagnose / Fix | Browser -> `:8642` directly (CORS) | Browser -> Recon -> `:8642` |
| This Python service | Same plugin/RAG/remediation code | **Unchanged internally**; only the caller moved to Recon |

Priyesh: your `app/plugins/*`, `app/rag/*`, and `/api/v1/diagnose` +
`/api/v1/remediate` contracts stay the same. Recon's `RagServiceClient` forwards
Prometheus-shaped alert JSON to those endpoints.

### Compose files involved

```bash
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml:rag-service.yaml
./run.sh -d
```

| File | Role |
|---|---|
| `monitoring.yaml` | Prometheus + Alertmanager services |
| `prometheus.yml` | Scrapes OM/SCM/DN; loads `ozone-aiops-alerts.yml`; sends alerts to Alertmanager |
| `ozone-aiops-alerts.yml` | Three alert rules (OM/SCM/datanode); each rule derives severity (low/medium/high/critical) from how long the condition has held |
| `alertmanager.yml` | Webhook to Recon; inhibition so only the highest severity tier notifies |
| `alertmanager.yml` | Webhook receiver -> `http://recon:9888/api/v1/aiops/webhook` |
| `rag-service.yaml` | Builds `recon-rag` container; sets `ozone.recon.aiops.*` on Recon |

Recon env (from `rag-service.yaml`):

- `ozone.recon.aiops.enabled=true`
- `ozone.recon.aiops.rag-service.endpoint=http://recon-rag:8642`

### Recon AIOps REST API (Java gateway)

Base path: `/api/v1/aiops`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhook` | Alertmanager ingestion (also usable for manual test payloads) |
| `GET` | `/alerts` | List alerts stored in RocksDB |
| `POST` | `/alerts/{id}/diagnose` | Forward alert to this service's `/api/v1/diagnose` |
| `POST` | `/alerts/{id}/remediate?dryRun=true&actionId=...` | Forward to `/api/v1/remediate` |

### Recon UI

Open **http://localhost:9888/#/Alerts** (HashRouter). The Alerts page appears in
the left nav on both New UI and Old UI.

Rebuild Recon UI after frontend changes (do **not** pass `-DskipRecon`):

```bash
mvn clean install -Pdist -DskipTests -DskipShade -DskipDocs
cd hadoop-ozone/dist/target/ozone-*-SNAPSHOT/compose/ozone
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml:rag-service.yaml
./run.sh -d
```

### Manual curl checks (from the host)

```bash
# List persisted alerts (use id from response for diagnose/remediate)
curl -s http://localhost:9888/api/v1/aiops/alerts | python3 -m json.tool

ALERT_ID=<id-from-above>
curl -s -X POST "http://localhost:9888/api/v1/aiops/alerts/${ALERT_ID}/diagnose" \
  | python3 -m json.tool

curl -s -X POST \
  "http://localhost:9888/api/v1/aiops/alerts/${ALERT_ID}/remediate?dryRun=true&actionId=increase_key_deleting_limit_per_task" \
  | python3 -m json.tool
```

Direct calls to this service (bypassing Recon) still work for local debugging:

```bash
curl -s http://localhost:8642/api/v1/health
curl -s http://localhost:8642/api/v1/plugins
```

### Known limitations

- **`config_client.py` tries JSON first and falls back to XML** when services
  return the default `/conf` format. Missing properties still produce warnings
  and default-based remediation plans.
- **Live remediation (`dryRun=false`)** is not implemented (HTTP 501).
- **One deletion plugin** ships today, registered for three Prometheus
  alertnames: `OzoneOmDeletionNotProgressing`, `OzoneScmDeletionNotProgressing`,
  and `OzoneDatanodeDeletionNotProgressing` (legacy `OzoneDeletionNotProgressing`
  still maps to OM).

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

`app/plugins/deletion_not_progressing.py` is the one shipped plugin, registered
for the three deletion-stuck alertnames above. It reads hop-specific JMX and
config — OM (`DeletingServiceMetrics`), SCM
(`SCMBlockDeletingService` + `hdds.scm.block.deletion.*`), or datanode
(`BlockDeletingService` + `ozone.block.deleting.*`) — and proposes the
matching dry-run remediation for that hop.

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
- [x] Phase 5: Alertmanager webhook -> Recon RocksDB persistence; Recon as
      Java gateway to this service (browser no longer calls `:8642` directly).
- [ ] Deferred: live remediation execution (`dryRun=false`) against a real
      cluster, gated behind explicit operator opt-in and an audit trail.
- [ ] Deferred: additional plugins beyond `DeletionNotProgressing` (the
      ABC/registry already generalize to this).
- [ ] Deferred: a curated, larger knowledge base beyond the two illustrative
      runbook documents shipped here.
- [ ] Deferred: fix OM `/conf` XML parsing in `config_client.py` so diagnose
      and remediate plans use live config values.
