---
title: AIOps Self-Healing for Apache Ozone — Alertmanager Ingestion into Recon, Diagnosed by a RAG-Grounded Python Service
summary: Add a self-healing layer where Prometheus evaluates alerting rules and Alertmanager routes fired alerts via webhook to a new Recon (Java) endpoint, which persists them in RocksDB and, on request, calls a standalone Python FastAPI microservice (recon-rag-service) that diagnoses each alert with a pluggable, retrieval-augmented pipeline and proposes dry-run-only remediations, surfaced in a new Alerts page in the Recon UI.
date: 2026-09-08
jira: HDDS-XXXXX
status: Proposed
author: Gargi Jaiswal
---

<!--
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License. See accompanying LICENSE file.
-->

## Contents

- [Problem Statement](#problem-statement)
- [Motivation](#motivation)
- [Scope](#scope)
- [Architecture Overview](#architecture-overview)
- [Background](#background)
- [Use-cases](#use-cases)
- [Solution](#solution)
  - [Docker Compose: Prometheus, Rule Evaluation, and the RAG Service Add-on](#docker-compose-prometheus-rule-evaluation-and-the-rag-service-add-on)
  - [Alert Delivery: Prometheus, Alertmanager, and the Recon Webhook](#alert-delivery-prometheus-alertmanager-and-the-recon-webhook)
  - [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb)
  - [recon-rag-service: a Standalone Python Diagnosis Microservice](#recon-rag-service-a-standalone-python-diagnosis-microservice)
  - [Pluggable Diagnostic Plugins](#pluggable-diagnostic-plugins)
  - [Retrieval Augmented Generation Pipeline](#retrieval-augmented-generation-pipeline)
  - [Dry-Run-Only Remediation](#dry-run-only-remediation)
  - [REST API Surface](#rest-api-surface)
  - [Diagnosis and Remediation Flow](#diagnosis-and-remediation-flow)
  - [Frontend Alerts Page](#frontend-alerts-page)
  - [Why Python for Diagnosis, Integrated with Java Ozone](#why-python-for-diagnosis-integrated-with-java-ozone)
  - [Configuration](#configuration)
  - [File Structure](#file-structure)
- [Existing Industry Standards](#existing-industry-standards)

---

## Problem Statement

Ozone cluster degradations — deletion slowdowns, replication imbalances, storage exhaustion, and read-latency spikes — are silent. Operators discover problems only after the cluster has been in a degraded state for hours or days. By that point, recovering the cluster requires extensive log analysis, configuration archaeology, and manual trial-and-error. The delay between onset and discovery causes unnecessary data-loss risk, operator overload, and customer escalations.

---

## Motivation

### Silent degradation goes undetected until it is critical

Ozone already emits detailed Prometheus metrics from every service (OM, SCM, datanode, S3 Gateway). None of those metrics are evaluated automatically for anomalies. An operator must know which metric to look at, know what a bad value looks like, and happen to be watching. Most degradations are caught only during a customer-reported incident.

### Root-cause analysis is time-consuming and requires deep expertise

When a degradation is finally found, diagnosing it requires correlating metrics and configuration across a single subsystem (for example, the OM key-deletion pipeline). An operator unfamiliar with the specific subsystem can take hours to trace the cause and to recall which of a dozen `ozone.*` configuration properties is relevant.

### Remediation is manual, error-prone, and slow

Workarounds such as tuning deletion-service batch sizes must be looked up and applied by hand. There is no in-product guidance or suggested fix, even for well-understood problems with known remedies.

### Alert detection belongs in Prometheus and Alertmanager; diagnosis belongs in a purpose-built service

Prometheus and Alertmanager are the proven tools for time-series threshold evaluation, deduplication, grouping, and delivery. Recon should not duplicate that: it should receive alerts Alertmanager has already deduplicated and grouped, over a single webhook, and own only what comes after — persisting what it received and diagnosing it. Diagnosis and remediation planning are a separate concern again, delegated to a dedicated service rather than reimplemented in Recon's JVM.

### Storage must match the scale of what is actually being stored

Ozone clusters at petabyte scale do not need another relational table for alert metadata, and alert volume is small and event-shaped. Alerts must still survive a Recon restart — an operator should not lose the record of what fired overnight just because Recon happened to restart — so Recon persists what the webhook delivers. The design must avoid coupling that durability layer to Recon's embedded Derby SQL store, which does not scale for HA or high write rates; RocksDB, already embedded in Recon for container-key mappings and namespace metadata, is the target instead. See [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb).

---

## Scope

> **_NOTE:_** This section describes the target design. Priyesh's prototype code, already on this branch, currently takes a shortcut for testing the RAG pipeline in isolation — the browser polls Recon's existing generic `MetricsProxyEndpoint` for Prometheus's raw alert list, and calls `recon-rag-service` directly. That shortcut is being replaced by the Alertmanager + Recon-webhook + RocksDB path below; the Python diagnosis/remediation pipeline itself does not change. See [Alert Delivery](#alert-delivery-prometheus-alertmanager-and-the-recon-webhook) for the exact delta.

**In scope (implemented):**

- A Prometheus alerting rule, `ozone-aiops-alerts.yml`, loaded via `rule_files` in `prometheus.yml`, evaluated by the Prometheus container the `monitoring.yaml` compose add-on already runs.
- `recon-rag-service`: a standalone Python/FastAPI microservice (dist compose add-on `rag-service.yaml`) that diagnoses one alert per request and proposes a dry-run-only remediation.
- A pluggable per-`alertname` diagnostic plugin architecture (`AlertDiagnosticPlugin`), collecting live evidence directly from the OM's standard `/jmx` and `/conf` HTTP servlets — the same endpoints any operator or monitoring tool can already call.
- A retrieval-augmented generation (RAG) pipeline: a swappable vector store (dependency-free in-memory default, optional Chroma) and a swappable LLM client (deterministic mock default, optional OpenAI-compatible endpoint), grounded in a small Markdown knowledge base shipped with the service.
- A dry-run-only remediation executor: validates a requested `action_id` against the plugin's fixed allowlist and returns a plan describing the change. It never mutates the cluster; `dryRun=false` is rejected with HTTP 501 in this build.
- One shipped plugin: `OzoneDeletionNotProgressing`, covering the "deletion slow or stuck" use-case end to end.

**In scope (target design, not yet implemented):**

- **Alertmanager**, added to the compose monitoring stack, routing fired alerts to a new Recon webhook.
- A new Recon (Java) endpoint, `POST /api/v1/aiops/webhook`, that receives Alertmanager's payload.
- **RocksDB-backed persistence** of alert state inside Recon's existing `ReconDBProvider`, written directly by the webhook handler, so alerts survive a Recon restart. See [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb).
- Recon (Java) REST endpoints, `GET /api/v1/aiops/alerts`, `POST /api/v1/aiops/alerts/{id}/diagnose`, and `POST /api/v1/aiops/alerts/{id}/remediate`, that read from RocksDB and, for diagnose/remediate, call `recon-rag-service` server-side (Java → Python HTTP call) and return its response. The frontend talks only to Recon; it no longer calls `recon-rag-service`'s published port directly.
- A new **Alerts** page in the existing (v1) Recon web UI, pointed at Recon's own `/api/v1/aiops/*` endpoints instead of the generic metrics proxy and the direct `recon-rag-service` calls used for prototyping.
- Additional plugins for under-replication, over-replication, read-latency, and storage-usage alerts, using the same `AlertDiagnosticPlugin` contract (see [Use-cases](#use-cases)).

**Out of scope for this design:**

- A Java, LangChain4j-based tool-calling agent — an earlier draft of this design proposed one; diagnosis logic stays in the Python `recon-rag-service` described here because the RAG/LLM tooling ecosystem is Python-first and iterates faster there (see [Files explicitly not in this design](#files-explicitly-not-in-this-design)).
- Live/automatic remediation of any kind — this build only ever plans a change; applying it is a future, explicitly-gated iteration (`RAG_ALLOW_LIVE_REMEDIATION`, unset by default).
- ML-model training or statistical anomaly detection.
- Multi-tenancy or per-volume/per-bucket alert scoping.
- Additional Alertmanager notification channels (email, PagerDuty, Slack) beyond the Recon webhook receiver.

---

## Architecture Overview

```
 Ozone services expose /prom on each host
 ┌──────┐  ┌──────┐  ┌────────────┐  ┌──────┐
 │  OM  │  │ SCM  │  │  Datanode  │  │ S3GW │
 └──┬───┘  └──┬───┘  └─────┬──────┘  └──┬───┘
    └──────────┴────────────┴─────────────┘
                     │  scrape /prom (docker-compose.yaml + monitoring.yaml)
                     ▼
          ┌──────────────────────┐
          │  Prometheus Server   │  evaluates ozone-aiops-alerts.yml
          └──────────┬───────────┘
                     │  alert fires
                     ▼
          ┌──────────────────────┐
          │    Alertmanager      │  dedup, grouping, routing
          └──────────┬───────────┘
                     │  POST webhook (v4 JSON payload)
                     ▼
   ┌─────────────────────────────────────────────┐
   │  Recon (Java)  POST /api/v1/aiops/webhook    │
   │  upserts alert into RocksDB (ReconDBProvider)│
   └────────────────────┬──────────────────────────┘
                        │
                        ▼
   ┌─────────────────────────────────────────────┐
   │  Recon (Java)  AIOpsEndpoint /api/v1/aiops   │
   │  GET  /alerts                    — from RocksDB
   │  POST /alerts/{id}/diagnose      — calls recon-rag-service
   │  POST /alerts/{id}/remediate     — calls recon-rag-service
   └────────────────────┬──────────────────────────┘
                        │
             ┌──────────┴──────────┐
             │  Recon "Alerts" page │  (browser talks only to Recon;
             │  (v1 UI)              │   no direct calls to :8642)
             └──────────────────────┘
                        │
                        │ server-side HTTP call
                        ▼
     ┌───────────────────────────────────────────┐
     │        recon-rag-service (Python/FastAPI)  │
     │                                             │
     │  PluginRegistry: labels.alertname -> plugin │
     │  plugin.collect_context() --GET--> OM       │
     │    /jmx?qry=...        /conf?format=json    │
     │                                             │
     │  RagPipeline.diagnose():                    │
     │    VectorStore.query(retrieval_query)        │
     │    + LLMClient.generate(prompt) -> JSON      │
     │                                             │
     │  RemediationExecutor.validate_and_plan():   │
     │    action_id in plugin.permitted_actions()? │
     │    -> RemediationPlan (dry-run, unapplied)  │
     └───────────────────────────────────────────┘
```

**Key design decisions:**

> **_NOTE:_** Alert detection lives in a Prometheus rules YAML file; delivery to Recon goes through Alertmanager and a webhook, not a poll of Prometheus's raw alert list. This matches the original design intent (dedup/grouping/routing belong to Alertmanager) rather than Priyesh's direct-poll test harness.

> **_NOTE:_** Alert state is written to RocksDB by the webhook handler itself, so it survives a Recon restart. See [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb).

> **_NOTE:_** Diagnosis and remediation logic stays in the separate Python process (`recon-rag-service`) — Priyesh's plugin + RAG pipeline is reused unmodified. What changes is *who calls it*: Recon's Java `AIOpsEndpoint` calls it server-side and relays the response, instead of the browser calling its published port directly.

> **_NOTE:_** The diagnosis pipeline itself is a single deterministic pass per request — collect evidence, retrieve knowledge-base context, one LLM call, parse and validate — not a multi-step tool-calling agent. See [recon-rag-service: a Standalone Python Diagnosis Microservice](#recon-rag-service-a-standalone-python-diagnosis-microservice) for why this is simpler than an agent and is enough for the alert types in scope.

> **_NOTE:_** Remediation is dry-run only. The executor never mutates the cluster in this build.

---

## Background

### How Prometheus metrics are exposed in Ozone

Each Ozone service (OM, SCM, datanode, S3 Gateway, Recon) extends `BaseHttpServer`, which registers `PrometheusMetricsSink` and mounts `PrometheusServlet` at `/prom`.

| Service | Default port | Scrape endpoint |
|---------|-------------|-----------------|
| OM | 9874 | `http://<om>:9874/prom` |
| SCM | 9876 | `http://<scm>:9876/prom` |
| Datanode | 9882 | `http://<dn>:9882/prom` |
| S3 Gateway | 19878 | `http://<s3g>:19878/prom` |
| Recon | 9888 | `http://<recon>:9888/prom` |

Ozone uses Hadoop Metrics2 with `PrometheusMetricsSink`, which normalises CamelCase to `snake_case` (for example, `DeletingServiceMetrics.numKeysPurged` becomes `deleting_service_metrics_num_keys_purged`). The `docker-compose.yaml` + `monitoring.yaml` compose add-on (`export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml`) already scrapes all five of these targets — no scrape-config change was needed for this feature; see [Docker Compose](#docker-compose-prometheus-rule-evaluation-and-the-rag-service-add-on).

### Alertmanager webhook payload

When an alert fires, Alertmanager sends a POST to configured webhook receivers with a v4 JSON payload. Each alert entry includes:

- **labels** — includes `alertname` (the rule's name) plus any labels the rule sets, e.g. `severity`, `component`.
- **annotations** — `summary`, `description` (also rule-defined).
- **status** — `firing` or `resolved`.
- **startsAt** / **endsAt** — timestamps.

`recon-rag-service`'s existing `AlertPayload` model (`labels`, `annotations`, `state`, `activeAt`) already mirrors Prometheus's/Alertmanager's per-alert shape closely enough that Recon's webhook handler can map one onto the other and forward it unmodified when calling `recon-rag-service` for diagnosis.

### Existing Recon infrastructure this design extends

**`MetricsProxyEndpoint`** (`org.apache.hadoop.ozone.recon.api`) — a generic `GET /api/v1/metrics/{api}` passthrough to whatever Prometheus API path `{api}` names, backed by `PrometheusServiceProviderImpl.getMetricsResponse(api, query)`. Priyesh's prototype used this (called with `api=alerts`) so the Alerts page could list alerts without any new Recon code, before the webhook/RocksDB path existed. It remains available for other Prometheus queries but is no longer how the Alerts page lists alerts in the target design — that becomes `GET /api/v1/aiops/alerts`, served from RocksDB.

**`ReconDBProvider`** — the RocksDB store already used by Recon for container-key mappings and namespace metadata. This design adds a dedicated column family for alert state. See [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb).

**OM's `/jmx` and `/conf` HTTP servlets** — the standard Hadoop `JMXJsonServlet` and `HddsConfServlet`, already exposed by every Ozone HTTP server. `recon-rag-service` reads live metrics and configuration straight from these, the same way any external monitoring tool could, instead of adding a new Java RPC or REST surface on the OM side. This does not change in the target design.

> The `recon-rag-service` implementation described in this document (the Python microservice, its plugin architecture, and the RAG pipeline) was built by Priyesh Karatha as part of this hackathon effort. This design doc reuses that implementation unchanged and describes the Alertmanager/webhook/RocksDB path in Recon that now sits in front of it.

---

## Use-cases

**Implemented in this hackathon iteration:** only the first use-case below (`OzoneDeletionNotProgressing`) has a shipped plugin end to end. The remaining four describe the target shape of the plugin architecture and are not yet built — adding one is "write a new plugin module," not "change the pipeline" (see [Pluggable Diagnostic Plugins](#pluggable-diagnostic-plugins)).

### Deletion slow or stuck (implemented)

An operator deletes a large number of keys. The OM's `KeyDeletingService` should steadily purge them; sometimes it stalls or falls behind.

**With this feature:** Prometheus fires `OzoneDeletionNotProgressing` when `DeletingServiceMetrics.numKeysPurged` has a zero purge rate for 5 minutes while there are more processed than purged keys (a backlog is present but not draining). Alertmanager POSTs it to Recon's webhook, which persists it in RocksDB. The operator sees the alert on the Alerts page. **Diagnose** calls Recon's `POST /api/v1/aiops/alerts/{id}/diagnose`, which forwards the stored alert to `recon-rag-service`. Its `DeletionNotProgressingPlugin` reads the OM's `DeletingServiceMetrics` JMX bean and the relevant `ozone.*.deleting.*` config properties, retrieves the matching runbook/config-reference excerpts, and returns a diagnosis, evidence, and (if applicable) a recommended fix, which Recon relays back to the UI. **Fix** requests a dry-run plan that doubles `ozone.key.deleting.limit.per.task` — the plan is displayed, never applied.

### Containers under-replicated (planned)

A datanode goes offline or decommissions slowly. Without alerting, the operator finds out when a client read fails. A future plugin would read SCM's under-replicated queue size and datanode health to tell apart a dead node from a slow replication queue, and propose replication-manager tuning or a runbook when a human decommission is needed.

### Containers over-replicated (planned)

After cluster expansion, some containers have excess replicas and waste storage. A future plugin would read SCM's over-replicated queue size and propose replication-manager tuning to speed up the drain.

### Read latency degraded (planned)

A hotspot, failing disk, or saturated Ratis pipeline causes client read latency to spike. A future plugin would correlate datanode and pipeline metrics to point at the likely hotspot, and propose a datanode-tuning fix or a manual-drain runbook.

### Storage utilization high (planned)

The cluster approaches capacity. Without alerting, writes fail at 100%. A future plugin would check capacity-growth trend and whether a deletion backlog is contributing, proposing accelerated deletion or a capacity-planning runbook.

---

## Solution

### Docker Compose: Prometheus, Rule Evaluation, and the RAG Service Add-on

`hadoop-ozone/dist/src/main/compose/ozone/` already wires Prometheus to OM, SCM, datanode(s), S3 Gateway, and Recon:

```
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml
./run.sh -d
```

`monitoring.yaml` adds the `prometheus`, `grafana`, and `jaeger` services and injects `monitoring.conf` into `datanode`, `om`, `scm`, `s3g`, and `recon`; `prometheus.yml`'s `scrape_configs` already lists all five as targets on their `/prom` paths. This part required no change for this feature.

**What this design adds:** Prometheus was scraping metrics but evaluating no alerting rules at all — there was no `rule_files` entry, so `OzoneDeletionNotProgressing` (the alertname the shipped plugin already expects) could never actually fire.

- **New file:** `ozone-aiops-alerts.yml` — one alerting rule group with the `OzoneDeletionNotProgressing` rule:

  ```yaml
  groups:
    - name: ozone-aiops-alerts
      rules:
        - alert: OzoneDeletionNotProgressing
          expr: >-
            rate(deleting_service_metrics_num_keys_purged[10m]) == 0
            and
            deleting_service_metrics_num_keys_processed > deleting_service_metrics_num_keys_purged
          for: 5m
          labels:
            severity: warning
            component: om
          annotations:
            summary: "Ozone Manager key deletion is not progressing"
            description: >-
              KeyDeletingService has keys queued for purge
              (numKeysProcessed > numKeysPurged) but the purge rate has been
              zero for 5 minutes.
  ```

- **Modified `prometheus.yml`:** added `rule_files: [/etc/ozone-aiops-alerts.yml]`.
- **Modified `monitoring.yaml`:** mounted the new file into the `prometheus` container alongside the existing `prometheus.yml` mount.

`alertname` must match a plugin's declared `alert_type` exactly (`app/plugins/deletion_not_progressing.py`'s `DeletionNotProgressingPlugin.alert_type`), since that string is how `recon-rag-service` picks which plugin diagnoses the alert.

A third, independent add-on brings up the diagnosis service itself:

```
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml:rag-service.yaml
./run.sh -d
```

`rag-service.yaml` builds and runs the `recon-rag-service` container (port 8642, published), points it at `om:9874`, `scm:9876`, and `recon:9888`, and sets `ozone.recon.prometheus.http.endpoint=http://prometheus:9090` on the `recon` service so `MetricsProxyEndpoint` has a Prometheus to proxy to.

---

### Alert Delivery: Prometheus, Alertmanager, and the Recon Webhook

**Component:** `AIOpsEndpoint` — path `POST /api/v1/aiops/webhook` (new Java code).

Alertmanager is configured (`alertmanager.yml`, added alongside the other compose assets under `hadoop-ozone/dist/src/main/compose/ozone/`) with a `recon-aiops` receiver whose webhook URL is `http://recon:9888/api/v1/aiops/webhook`, and routes the OM/SCM/datanode alerts defined in `ozone-aiops-alerts.yml` to it, with `send_resolved: true` so Recon also learns when an alert clears.

**Webhook handler behavior:**

1. Parse the Alertmanager v4 payload and iterate the `alerts` array.
2. For each entry, extract `labels`, `annotations`, `status`, and `startsAt`/`endsAt`.
3. Upsert it into RocksDB, keyed by a stable fingerprint of its label set (see [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb)).
4. Return HTTP 200 so Alertmanager does not retry unnecessarily.

This replaces Priyesh's prototype wiring, where the Alerts page polled Recon's generic `MetricsProxyEndpoint` for Prometheus's raw `/api/v1/alerts` on an interval. That worked for exercising the RAG pipeline in isolation, but it means alert state only ever exists as long as Prometheus itself is up and hasn't rotated it out — Alertmanager plus a durable webhook receiver is the version that matches the original design intent: Prometheus detects, Alertmanager dedups/groups/routes, Recon persists and diagnoses.

---

### Alert Persistence in RocksDB

**Component:** a new column family, `aiops_alerts`, in Recon's existing `ReconDBProvider`.

- **Key:** a stable fingerprint of the alert's label set (e.g. a hash of `labels`, sorted) — the same alert firing again produces the same key, so the webhook's upsert overwrites rather than duplicates.
- **Value:** the `AlertPayload`-shaped JSON (`labels`, `annotations`, `state`, `activeAt`) plus `status` (`firing`/`resolved`) and `updatedAtMs`.
- **Written by:** the webhook handler on every delivery (`firing` upserts, `resolved` marks the record resolved rather than deleting it, so recently-cleared alerts remain visible for a while).
- **Read by:** `GET /api/v1/aiops/alerts`, which lists the RocksDB contents directly — no live Prometheus call in this path.

**Why RocksDB and not an in-memory map:** the whole point of this section is that a Recon restart must not lose track of what alerts fired. An in-memory `ConcurrentHashMap` populated only by webhook deliveries would be empty after every restart, with nothing to resync from except Alertmanager's own state (itself not durable across an Alertmanager restart). Writing directly to RocksDB — already embedded in Recon, already used for other derived metadata — avoids adding a second moving part just to get durability.

**Why not a new SQL table:** alert volume is small (bounded by rule count × affected components, not by cluster data size) and access is pure key-value (upsert by fingerprint, list all); this doesn't need Derby/PostgreSQL's relational features, and a new SQL table would mean schema migration and jOOQ codegen for no benefit over a RocksDB column family.

**Diagnoses are not persisted yet.** `POST /api/v1/aiops/alerts/{id}/diagnose` re-collects evidence from OM and re-runs the RAG pipeline on every call (see [recon-rag-service: a Standalone Python Diagnosis Microservice](#recon-rag-service-a-standalone-python-diagnosis-microservice)); only the alert itself is cached. Caching diagnoses (and a heal audit trail, once live remediation exists) would follow the same RocksDB pattern, in additional column families, as a later iteration.

---

### recon-rag-service: a Standalone Python Diagnosis Microservice

**Why a single deterministic pass instead of a multi-step agent:** an earlier draft proposed a LangChain4j tool-calling agent that would decide, turn by turn, which metric to check next. The implemented design instead gives each alert type a plugin that declares up front exactly what evidence it needs (`collect_context`) and exactly what it's allowed to fix (`permitted_actions`). For an alert type like `OzoneDeletionNotProgressing`, the right evidence (the `DeletingServiceMetrics` JMX bean plus a handful of `ozone.*.deleting.*` properties) is known ahead of time — a fixed collection step plus one grounded LLM call is enough, and is far simpler to reason about, test, and bound than an open-ended agent loop. Additional plugins can still each define their own collection logic; what's shared is the retrieval + generation + allowlist-validation pipeline (`RagPipeline`, `RemediationExecutor`).

**Caller:** this service's `/api/v1/diagnose` and `/api/v1/remediate` endpoints are unchanged from Priyesh's implementation, but in the target design their caller is Recon's `AIOpsEndpoint` (a server-side Java HTTP call), not the browser — see [Alert Delivery](#alert-delivery-prometheus-alertmanager-and-the-recon-webhook) and [Why Python](#why-python-for-diagnosis-integrated-with-java-ozone). Nothing inside `recon-rag-service` needs to change for this; it already just receives an `AlertPayload` over HTTP and doesn't care who sent it.

**Request flow for `POST /api/v1/diagnose`:**

1. `PluginRegistry.get_plugin(alert.alert_type)` looks up the plugin registered for `labels.alertname` (404 if none).
2. `plugin.collect_context(alert, cluster)` gathers evidence — for the shipped plugin, `GET om:9874/jmx?qry=Hadoop:service=OzoneManager,name=DeletingServiceMetrics` and `GET om:9874/conf?format=json` filtered to a named allowlist of properties.
3. `RagPipeline.diagnose(context, plugin)`:
   - `plugin.retrieval_query(context)` builds a text query from the collected evidence.
   - `VectorStore.query(query, top_k=3)` retrieves the closest knowledge-base chunks.
   - A single prompt (evidence + retrieved excerpts + the required JSON output schema) is sent to `LLMClient.generate(prompt)`.
   - The JSON reply is parsed into a `DiagnosisResponse`; any `recommended_fix.action_id` not in `plugin.permitted_actions()` is dropped (not surfaced), regardless of what the model proposed.
4. The response — `diagnosis`, `evidence`, optional `recommended_fix`, `retrieved_documents` — is returned to its caller (Recon's `AIOpsEndpoint`, which relays it to the browser). The diagnosis itself is not persisted by either side yet — see [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb).

---

### Pluggable Diagnostic Plugins

**Component:** `app/plugins/base.py`'s `AlertDiagnosticPlugin` (an `ABC`), registered via `app/plugins/registry.py`'s `@register_plugin` class decorator — the Python analogue of the `ServiceLoader`/SPI pattern already used in Ozone (e.g. `OmTransportFactory.createFactory`). Adding a new alert type means adding a new module and decorating its plugin class, not editing a central dispatcher.

Every plugin implements:

- `alert_type` — the Prometheus `labels.alertname` it handles.
- `collect_context(alert, cluster)` — gather JMX metrics / config properties into a `DiagnosticContext`.
- `retrieval_query(context)` — text used to search the knowledge base.
- `permitted_actions()` — the fixed allowlist of remediation `ActionSpec`s this plugin may ever propose.
- `build_remediation_plan(action_id, context)` — describe what an action would change, without applying it.

**Shipped plugin:** `DeletionNotProgressingPlugin` (`alert_type="OzoneDeletionNotProgressing"`). Its one permitted action, `increase_key_deleting_limit_per_task`, doubles `ozone.key.deleting.limit.per.task` from its current (or default) value.

---

### Retrieval Augmented Generation Pipeline

**Why RAG:** an LLM's memory of Ozone internals is unreliable — exact configuration keys, defaults, and thresholds are easy to get wrong. Retrieval grounds the diagnosis in real, curated Ozone reference material instead of the model inventing a plausible-looking config key.

**Vector store** (`app/rag/vector_store.py`, selected via `RAG_VECTOR_STORE_BACKEND`):

| Backend | Implementation | Notes |
|---|---|---|
| `memory` (default) | `InMemoryVectorStore` | Dependency-free hashing-trick bag-of-words embedding (`app/rag/embeddings.py`) + cosine similarity over `knowledge_base/*.md`. No ML runtime needed. |
| `chroma` | `ChromaVectorStore` | Real `chromadb` collection, persisted to `RAG_CHROMA_PERSIST_DIR`. `chromadb` is imported lazily so the default image doesn't need it installed. |

**Knowledge base** (`knowledge_base/*.md`, loaded and embedded once at process startup): `deletion_not_progressing_runbook.md` and `deletion_config_reference.md` today — the runbook and config reference for the one shipped plugin. Adding a plugin means adding its own runbook/reference doc(s) here too.

**LLM client** (`app/rag/llm_client.py`, selected by whether `RAG_LLM_BASE_URL`/`RAG_LLM_API_KEY` are set):

| Backend | Implementation | Notes |
|---|---|---|
| `MockLLMClient` (default) | Deterministic, rule-based | Parses the same JSON evidence block a real model would see out of the prompt and reasons over it directly — not a canned placeholder. Exercises the full pipeline (retrieval, prompt construction, JSON-schema parsing, allowlist enforcement) with no external dependency or API key. |
| `OpenAICompatibleLLMClient` | `httpx` POST to `{RAG_LLM_BASE_URL}/chat/completions` | Any OpenAI chat-completions-compatible endpoint (self-hosted vLLM/Ollama gateway, Azure OpenAI, OpenAI itself). |

**Prompt** (`app/rag/prompt_templates.py`): one system prompt plus a single user message containing a fenced JSON context block (alert labels/annotations, JMX metrics, config properties, notes, the plugin's permitted action IDs, retrieved source names) followed by the retrieved excerpts and the required output JSON schema (`diagnosis`, `evidence[]`, optional `recommended_fix`).

---

### Dry-Run-Only Remediation

**Component:** `app/remediation/executor.py`'s `validate_and_plan()`.

1. Reject `action_id` values not in `plugin.permitted_actions()` with `ActionNotPermittedError` → HTTP 400.
2. Reject `dryRun=false` with `LiveRemediationNotSupportedError` → HTTP 501, unless `RAG_ALLOW_LIVE_REMEDIATION` is set (no shipped configuration sets it — the flag exists purely as the documented seam for a future live-execution path).
3. Otherwise call `plugin.build_remediation_plan(action_id, context)`, which only ever *describes* a change (`config_changes`, `requires_restart`, `risk`, `warnings`) — it never calls out to the cluster.

This executor never mutates anything in this build, by construction: the only way to reach a real config write would be to both pass `dryRun=false` and have an operator explicitly set `RAG_ALLOW_LIVE_REMEDIATION`, and no plugin's `build_remediation_plan` currently performs a write even when `dry_run` is `False`.

---

### REST API Surface

**Recon (Java) — new, base path `/api/v1/aiops`, the only surface the frontend calls:**

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhook` | Receive Alertmanager webhook pushes; upsert into RocksDB. |
| GET | `/alerts` | List alert state from RocksDB. |
| POST | `/alerts/{id}/diagnose` | Look up the alert in RocksDB, call `recon-rag-service`'s `/api/v1/diagnose` server-side, relay the `DiagnosisResponse`. |
| POST | `/alerts/{id}/remediate?dryRun=true` | Look up the alert, call `recon-rag-service`'s `/api/v1/remediate`, relay the `RemediationPlan`. |

**`recon-rag-service` (Python) — unchanged from Priyesh's implementation, prefix `/api/v1`, now called by Recon rather than the browser:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check. |
| GET | `/plugins` | List registered `alert_type`s. |
| POST | `/diagnose` | Body: a `AlertPayload` (mirrors a Prometheus/Alertmanager alert). Returns a `DiagnosisResponse`. |
| POST | `/remediate?dryRun=true` | Body: `{alert, action_id}`. Returns a `RemediationPlan`. `dryRun=false` → 501. |

`recon-rag-service`'s port no longer needs to be published to the browser once Recon mediates every call, and its CORS configuration becomes unnecessary for this integration (Recon and `recon-rag-service` talk server-to-server inside the compose network). See [`recon-rag-service/openapi/rag-service.openapi.yaml`](../../../../hadoop-ozone/dist/src/main/compose/ozone/recon-rag-service/openapi/rag-service.openapi.yaml) for its full contract.

---

### Diagnosis and Remediation Flow

Both calls are stateless and independent on the `recon-rag-service` side — `/remediate` does not reuse anything computed by a prior `/diagnose` call for the same alert; it re-runs `plugin.collect_context()` from scratch. Recon adds one hop in front of each, but does not change that.

**Diagnose:** browser → Recon `POST /api/v1/aiops/alerts/{id}/diagnose` → Recon loads the alert from RocksDB by `{id}` → Recon `POST recon-rag-service:8642/api/v1/diagnose` (the stored alert) → plugin lookup → `collect_context` → `RagPipeline.diagnose` → `DiagnosisResponse` → Recon relays it to the browser → rendered in an expandable row (Diagnosis / Evidence / Recommended Fix).

**Fix (dry run):** browser → Recon `POST /api/v1/aiops/alerts/{id}/remediate?dryRun=true` (with the `recommended_fix.action_id` from the diagnosis) → Recon loads the alert from RocksDB → Recon `POST recon-rag-service:8642/api/v1/remediate?dryRun=true` → plugin lookup → `collect_context` → `validate_and_plan` → `RemediationPlan` → Recon relays it to the browser → rendered as a description, the proposed config changes, and any warnings. Nothing on the cluster changes.

---

### Frontend Alerts Page

**Location:** `hadoop-ozone/recon/src/main/resources/webapps/recon/ozone-recon-web/src/views/alerts/alerts.tsx` — the existing (v1) Recon web app, not Recon V2.

**Route:** `/Alerts`, added in `routes.tsx`. **Navigation:** a permanent `Alerts` item (`AlertOutlined` icon) in `navBar.tsx`.

**Behavior (target):**

- On mount and on an auto-reload interval (`AutoReloadHelper`, same pattern as other Recon pages), fetches `GET /api/v1/aiops/alerts` from Recon (RocksDB-backed) and renders one row per alert (alertname, severity, state, active-since, summary).
- Each row expands to a **Diagnose** button (calls `POST /api/v1/aiops/alerts/{id}/diagnose` on Recon); once a diagnosis returns, a **Fix (dry run)** button appears if a `recommended_fix` was returned (calls `POST /api/v1/aiops/alerts/{id}/remediate?dryRun=true` on Recon).
- The frontend no longer needs `RAG_SERVICE_BASE_URL` or a published `recon-rag-service` port — every call goes to Recon, same-origin as the rest of the Recon UI. Priyesh's prototype (`src/constants/ragService.constants.tsx`, calling `http://localhost:8642` directly with CORS) is superseded by this.
- A persistent banner states that Diagnose/Fix are a prototype and Fix always runs as a dry run.

---

### Why Python for Diagnosis, Integrated with Java Ozone

`recon-rag-service` is a standalone Python/FastAPI process, not a library embedded in Recon's JVM, for this hackathon:

- **Ecosystem fit.** FastAPI + `pydantic` gives request/response validation and OpenAPI generation for free; `httpx` is a small, synchronous-friendly HTTP client. The RAG/LLM tooling ecosystem (vector stores, embedding libraries, OpenAI-compatible clients) is Python-first, which matters for how fast this could be built and iterated on during the hackathon — this is the concrete reason diagnosis logic stays in Python rather than being ported to LangChain4j/Java.
- **Independent lifecycle.** The service has its own `Dockerfile` (`python:3.11-slim`, not the Ozone runner image), its own `requirements.txt`, and its own compose add-on (`rag-service.yaml`). It can be rebuilt, redeployed, and have its dependencies upgraded without touching Recon's build or release process.
- **Plain-HTTP integration boundary.** No new Java↔Python RPC bridge is needed anywhere in this design:
  - *Alertmanager → Recon:* a standard webhook POST, no Python involved.
  - *Recon → `recon-rag-service`:* Recon's Java `AIOpsEndpoint` makes a server-side HTTP call (a small internal client, analogous in shape to `PrometheusServiceProviderImpl`) to `recon-rag-service`'s `/api/v1/diagnose` and `/api/v1/remediate`, forwarding the stored alert and relaying the JSON response — no protobuf, no custom wire format.
  - *`recon-rag-service` → Ozone:* it reads OM's standard `/jmx` and `/conf` HTTP servlets — the same Hadoop-provided endpoints any external tool already uses — so no new Java-side API was needed on the OM side either.
  - *Browser → Recon:* the frontend only ever talks to Recon's own `/api/v1/aiops/*` endpoints, same-origin; it never calls `recon-rag-service` directly (that was Priyesh's prototype shortcut, not the target design).

The Java-side work this design still requires is the webhook handler, the RocksDB column family, the `AIOpsEndpoint` REST surface, and the small HTTP client that calls `recon-rag-service`; everything on the diagnosis/remediation side is the existing Python code, unmodified.

---

### Configuration

**Recon (`ozone-site.xml`, new):**

| Key | Default | Description |
|-----|---------|-------------|
| `ozone.recon.aiops.enabled` | `true` | Master toggle; when false, the webhook returns 503. |
| `ozone.recon.aiops.rag-service.endpoint` | (none) | Base URL of `recon-rag-service`, e.g. `http://recon-rag:8642`, used by `AIOpsEndpoint` for server-side diagnose/remediate calls. |

**`recon-rag-service` (env vars, see `app/config.py`):**

| Key | Default | Description |
|-----|---------|-------------|
| `OM_HTTP_ADDRESS` | `om:9874` | OM host:port for `/jmx` and `/conf` collection. |
| `SCM_HTTP_ADDRESS` | `scm:9876` | SCM host:port, for future plugins. |
| `RECON_HTTP_ADDRESS` | `recon:9888` | Recon host:port, for future plugins. |
| `RAG_VECTOR_STORE_BACKEND` | `memory` | `memory` (default) or `chroma`. |
| `RAG_KNOWLEDGE_BASE_DIR` | `/app/knowledge_base` | Directory of `*.md` files embedded at startup. |
| `RAG_CHROMA_PERSIST_DIR` | `/tmp/recon-rag-chroma` | Only used when `RAG_VECTOR_STORE_BACKEND=chroma`. |
| `RAG_LLM_BASE_URL` / `RAG_LLM_API_KEY` | (unset) | When both are set, uses `OpenAICompatibleLLMClient`; otherwise `MockLLMClient`. |
| `RAG_LLM_MODEL` | `gpt-4o-mini` | Model name sent to the chat-completions endpoint. |
| `RAG_LLM_TIMEOUT_SECONDS` | `30` | HTTP timeout for the LLM call. |
| `RAG_HTTP_CLIENT_TIMEOUT_SECONDS` | `10` | HTTP timeout for JMX/config collection calls to Ozone services. |
| `RAG_CORS_ALLOWED_ORIGINS` | `*` | CORS origins allowed to call this service directly from the browser. |
| `RAG_ALLOW_LIVE_REMEDIATION` | `false` | Seam for a future live-execution path; unused by any shipped plugin today. |

**Frontend:** no configuration needed — the Alerts page calls same-origin Recon endpoints. `RAG_SERVICE_BASE_URL` / `src/constants/ragService.constants.tsx` (Priyesh's direct-call prototype) is removed once the frontend is repointed at Recon.

**Graceful degradation:**

- `recon-rag-service` not running or `ozone.recon.aiops.rag-service.endpoint` not configured → Alerts page still lists alerts (from RocksDB); Diagnose/Fix return an error from Recon instead of a raw connection failure.
- Alertmanager not configured or not delivering → Alerts page stays empty (or stale) until a webhook arrives; there is no fallback poll of Prometheus in the target design.
- OM `/jmx` or `/conf` unreachable from `recon-rag-service` → `collect_context` degrades to notes-only evidence (`JmxFetchError`/`ConfigFetchError` become diagnosis notes, not 500s); the pipeline still runs and the mock/real LLM explains that telemetry could not be collected.
- No knowledge-base match → `retrieved_documents` is empty; the LLM still receives the collected evidence and the required output schema.

---

### File Structure

```
hadoop-ozone/
├── dist/src/main/compose/ozone/
│   ├── ozone-aiops-alerts.yml              # Prometheus alerting rule (OzoneDeletionNotProgressing)
│   ├── prometheus.yml                       # added rule_files
│   ├── monitoring.yaml                      # mount ozone-aiops-alerts.yml into prometheus
│   ├── alertmanager.yml                     # NEW (target): recon-aiops webhook receiver + route
│   ├── rag-service.yaml                     # recon-rag-service compose add-on (unchanged)
│   ├── README.md                            # documents the rag-service add-on
│   └── recon-rag-service/
│       ├── Dockerfile                       # python:3.11-slim, uvicorn entrypoint, port 8642
│       ├── README.md
│       ├── requirements.txt / requirements-dev.txt / pytest.ini
│       ├── openapi/rag-service.openapi.yaml
│       ├── knowledge_base/
│       │   ├── deletion_not_progressing_runbook.md
│       │   └── deletion_config_reference.md
│       ├── app/
│       │   ├── main.py                      # FastAPI app + CORS middleware
│       │   ├── config.py                    # env-var Settings
│       │   ├── models.py                    # AlertPayload, DiagnosticContext, DiagnosisResponse, RemediationPlan, ...
│       │   ├── api/routes.py                # /health, /plugins, /diagnose, /remediate
│       │   ├── collectors/
│       │   │   ├── jmx_client.py            # GET <host>/jmx
│       │   │   └── config_client.py         # GET <host>/conf?format=json (explicit property allowlist)
│       │   ├── plugins/
│       │   │   ├── base.py                  # AlertDiagnosticPlugin ABC
│       │   │   ├── registry.py              # @register_plugin, get_plugin, list_plugins
│       │   │   └── deletion_not_progressing.py  # shipped plugin
│       │   ├── rag/
│       │   │   ├── embeddings.py            # hashing-trick bag-of-words embedding
│       │   │   ├── vector_store.py          # InMemoryVectorStore, ChromaVectorStore
│       │   │   ├── llm_client.py            # MockLLMClient, OpenAICompatibleLLMClient
│       │   │   ├── prompt_templates.py
│       │   │   └── pipeline.py              # RagPipeline.diagnose()
│       │   └── remediation/executor.py      # validate_and_plan()
│       └── tests/
│
└── recon/src/main/java/org/apache/hadoop/ozone/recon/
    ├── api/
    │   ├── MetricsProxyEndpoint.java        # UNCHANGED; no longer used by the Alerts page
    │   └── AIOpsEndpoint.java               # NEW (target): webhook, alerts, diagnose, remediate
    └── aiops/                               # NEW (target)
        ├── AIOpsModule.java                 # Guice bindings
        ├── AlertStore.java                  # RocksDB-backed CRUD over the aiops_alerts column family
        ├── RagServiceClient.java            # HTTP client calling recon-rag-service's /diagnose, /remediate
        └── model/Alert.java                 # mirrors recon-rag-service's AlertPayload shape

hadoop-ozone/recon/src/main/resources/webapps/recon/ozone-recon-web/src/
├── views/alerts/
│   ├── alerts.tsx                           # Alert table + Diagnose/Fix, MODIFIED to call Recon's own /api/v1/aiops/*
│   └── alerts.less
├── routes.tsx                                # added /Alerts route
├── components/navBar/navBar.tsx              # added Alerts nav item
└── constants/breadcrumbs.constants.tsx       # added /Alerts breadcrumb
```

(`src/constants/ragService.constants.tsx` — Priyesh's `RAG_SERVICE_BASE_URL` prototype constant — is removed once `alerts.tsx` is repointed at Recon.)

**Files explicitly not in this design:**

| Path | Reason |
|------|--------|
| `recon-codegen/.../AIOpsSchemaDefinition.java` | No SQL table; alert persistence uses RocksDB (see [Alert Persistence in RocksDB](#alert-persistence-in-rocksdb)). |
| `AIOpsAgentService` / LangChain4j tool-calling agent / `@Tool` classes | An earlier draft proposed a Java agent with diagnostic and heal tools. Superseded by the Python `recon-rag-service`'s plugin + RAG pipeline: diagnosis logic stays in Python, Recon only routes to it. |
| Recon V2 `v2/pages/aiops/aiops.tsx` | The Alerts page lives in the v1 Recon web app (`src/views/alerts/`), not Recon V2. |
| Reimplementing `recon-rag-service`'s plugins/RAG pipeline in Java | Explicitly not done — Python is kept for this because the RAG/LLM ecosystem is Python-first; Recon integrates with it over HTTP instead. |

---

## Existing Industry Standards

**Prometheus Alertmanager** — the de-facto standard for rule-based alert deduplication, grouping, and notification routing, so Recon doesn't reimplement any of that. See [Prometheus Alertmanager documentation](https://prometheus.io/docs/alerting/latest/alertmanager/).

**Alertmanager webhook receiver** — the standard integration point for custom systems (PagerDuty, Grafana OnCall, OpsGenie). Recon's `AIOpsEndpoint` consumes the same v4 webhook format. See [webhook_config](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config).

**Retrieval-augmented generation (RAG)** — grounding an LLM's output in retrieved domain documents, instead of relying on the model's own memory, is standard practice for reducing invented facts in domain-specific answers. Used here to keep the diagnosis tied to real Ozone configuration reference material and runbooks.

**Plugin/SPI architecture (Strategy pattern)** — `AlertDiagnosticPlugin` + `@register_plugin` mirrors the `ServiceLoader`-based extension points already used elsewhere in Ozone (e.g. `OmTransportFactory.createFactory`): a fixed interface, new implementations register themselves, and the dispatcher never needs to change.

**RocksDB for operational metadata** — Ozone already uses RocksDB for OM, SCM, and Recon metadata at PB scale. Planned alert persistence follows the same pattern rather than adding a new SQL table.

**FastAPI + pydantic microservices** — a widely-used, typed, self-documenting (OpenAPI) pattern for small Python HTTP services, used here to keep `recon-rag-service` dependency-light and independently deployable from the Ozone/Recon JVM release cycle.
