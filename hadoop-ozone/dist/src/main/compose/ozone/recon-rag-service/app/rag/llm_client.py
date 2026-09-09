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
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict

import httpx

from app.config import settings
from app.rag.prompt_templates import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

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


_CONTAINER_HEALTH_ALERT_TYPES = {
    "OzoneScmContainerMissing": "missing",
    "OzoneScmContainerUnderReplicated": "under_replicated",
    "OzoneScmContainerUnhealthy": "unhealthy",
}


class MockLLMClient(LLMClient):
    """Deterministic rule-based diagnosis, used when no real LLM endpoint is
    configured. Not a literal placeholder -- it produces a genuine,
    metrics-grounded diagnosis, just without an actual model behind it."""

    def generate(self, prompt: str) -> str:
        context = _extract_context_block(prompt)
        alert_type = context.get("alert_type", "")
        health_state = _CONTAINER_HEALTH_ALERT_TYPES.get(alert_type)
        if health_state:
            return self._generate_container_health(context, health_state)
        return self._generate_deletion(context)

    def _generate_deletion(self, context: Dict[str, Any]) -> str:
        alert_labels = context.get("alert_labels", {})
        component = alert_labels.get("component", "om").lower()
        jmx_metrics = context.get("jmx_metrics", {})
        config_properties = context.get("config_properties", {})
        notes = context.get("notes", [])
        retrieved_sources = context.get("retrieved_sources", [])
        logger.info(
            "MockLLMClient.generate: component=%s jmx_metrics=%s config_properties=%s notes=%s",
            component, jmx_metrics, config_properties, notes,
        )

        evidence = list(notes)
        if jmx_metrics:
            evidence.append(f"Deletion-service JMX snapshot ({component}): {jmx_metrics}")
        if config_properties:
            evidence.append(f"Relevant configuration ({component}): {config_properties}")
        if retrieved_sources:
            evidence.append(f"Matched runbook(s): {', '.join(retrieved_sources)}")

        recommended_fix = None
        what_happened = ""
        why_it_happened = ""
        how_to_fix = ""
        if not jmx_metrics and not config_properties:
            what_happened = (
                f"The {component} deletion alert fired but recon-rag-service could "
                "not collect JMX metrics or configuration from the cluster."
            )
            why_it_happened = (
                "The JMX or /conf endpoint may be unreachable, misconfigured, or "
                "the deletion service may not have started on that component."
            )
            how_to_fix = (
                "Verify network connectivity to the component HTTP endpoint, confirm "
                "the deletion service is running, then retry diagnosis."
            )
        elif component == "scm":
            pending = jmx_metrics.get("numBlockDeletionTransactions")
            completed = jmx_metrics.get("NumBlockDeletionTransactionCompleted")
            what_happened = (
                "SCM block deletion is not progressing: the DeletedBlockLog backlog "
                f"is not draining (numBlockDeletionTransactions={pending}, "
                f"NumBlockDeletionTransactionCompleted={completed})."
            )
            why_it_happened = (
                "Datanodes may be unavailable or slow to acknowledge deletion "
                "commands, or hdds.scm.block.deletion.per-interval.max may be too "
                "low for the current backlog volume."
            )
            prop = "hdds.scm.block.deletion.per-interval.max"
            current_limit = config_properties.get(prop)
            proposed_limit = int(current_limit) * 2 if current_limit else 1000000
            how_to_fix = (
                f"Increase {prop} from {current_limit or 'default'} to "
                f"{proposed_limit} so SCM sends more block-deletion commands per "
                "interval. Also verify datanode health and deletion command acks."
            )
            recommended_fix = {
                "action_id": "increase_scm_block_deletion_per_interval_max",
                "summary": "Increase SCM block deletion throughput per interval.",
                "config_changes": {prop: str(proposed_limit)},
                "rationale": (
                    "Doubling the per-interval block limit lets SCM drain the "
                    "DeletedBlockLog faster when datanodes are healthy."
                ),
            }
        elif component == "datanode":
            pending = jmx_metrics.get("TotalPendingBlockCount")
            success = jmx_metrics.get("SuccessCount")
            instance = alert_labels.get("instance", "datanode")
            what_happened = (
                f"Datanode {instance} block deletion is stalled: pending blocks are "
                f"not being cleared (TotalPendingBlockCount={pending}, "
                f"SuccessCount={success})."
            )
            why_it_happened = (
                "ozone.block.deleting.service.interval may be too large, container "
                "locks may be timing out, or the datanode disk may be unhealthy."
            )
            prop = "ozone.block.deleting.service.interval"
            current_interval = config_properties.get(prop, "60s")
            proposed_interval = self._halve_duration(current_interval)
            how_to_fix = (
                f"Halve {prop} from {current_interval} to {proposed_interval} on "
                f"{instance} so BlockDeletingService runs more frequently. Check "
                "datanode logs for lock timeouts and disk errors."
            )
            recommended_fix = {
                "action_id": "decrease_datanode_block_deleting_interval",
                "summary": "Run BlockDeletingService more frequently on the datanode.",
                "config_changes": {prop: proposed_interval},
                "rationale": (
                    "Halving the service interval lets the datanode process its "
                    "local deletion backlog more often."
                ),
            }
        else:
            processed = jmx_metrics.get("NumKeysProcessed")
            purged = jmx_metrics.get("NumKeysPurged")
            what_happened = (
                "OM key deletion (KeyDeletingService) is not progressing: keys are "
                f"being processed slowly or not purged (NumKeysProcessed={processed}, "
                f"NumKeysPurged={purged})."
            )
            why_it_happened = (
                "ozone.key.deleting.limit.per.task may be too small for the backlog, "
                "or a downstream dependency (snapshot deep cleaning, SCM block "
                "deletion pipeline) may itself be stalled."
            )
            current_limit = config_properties.get("ozone.key.deleting.limit.per.task")
            proposed_limit = int(current_limit) * 2 if current_limit else 100000
            how_to_fix = (
                f"Increase ozone.key.deleting.limit.per.task from "
                f"{current_limit or 'default'} to {proposed_limit}. If the backlog "
                "persists, inspect SCM and datanode deletion metrics for downstream "
                "bottlenecks."
            )
            recommended_fix = {
                "action_id": "increase_key_deleting_limit_per_task",
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

        reply = json.dumps(
            {
                "what_happened": what_happened,
                "why_it_happened": why_it_happened,
                "how_to_fix": how_to_fix,
                "evidence": evidence,
                "recommended_fix": recommended_fix,
            }
        )
        logger.info("MockLLMClient.generate: reply=%s", reply)
        return reply

    @staticmethod
    def _halve_duration(value: str) -> str:
        match = re.fullmatch(r"(\d+)([smhd])", value.strip())
        if not match:
            return value
        amount = max(1, int(match.group(1)) // 2)
        return f"{amount}{match.group(2)}"

    def _generate_container_health(self, context: Dict[str, Any], health_state: str) -> str:
        jmx_metrics = context.get("jmx_metrics", {})
        config_properties = context.get("config_properties", {})
        notes = context.get("notes", [])
        retrieved_sources = context.get("retrieved_sources", [])

        evidence = list(notes)
        if jmx_metrics:
            evidence.append(f"ReplicationManagerMetrics snapshot: {jmx_metrics}")
        if config_properties:
            evidence.append(f"Relevant configuration: {config_properties}")
        if retrieved_sources:
            evidence.append(f"Matched runbook(s): {', '.join(retrieved_sources)}")

        metric_key = {
            "missing": "MissingContainers",
            "under_replicated": "UnderReplicatedContainers",
            "unhealthy": "UnhealthyContainers",
        }[health_state]
        count = jmx_metrics.get(metric_key)

        recommended_fix = None
        if not jmx_metrics:
            what_happened = (
                "The SCM container-health alert fired but recon-rag-service could "
                "not collect ReplicationManagerMetrics from SCM."
            )
            why_it_happened = (
                "The SCM JMX endpoint may be unreachable, or SCM may still be in "
                "safemode/starting up."
            )
            how_to_fix = "Verify connectivity to SCM's HTTP endpoint and retry diagnosis."
        elif health_state == "missing":
            what_happened = (
                f"SCM reports {count} container(s) with no online replicas "
                f"({metric_key}={count})."
            )
            why_it_happened = (
                "All datanodes holding a replica of these containers are "
                "unavailable (down, decommissioned, or with a failed volume), or "
                "the containers were under-replicated long enough that the last "
                "remaining copies were lost."
            )
            how_to_fix = (
                "Investigate datanode and disk health immediately for the "
                "affected containers (`ozone admin container info <id>`). This is "
                "an operator escalation; no automated config fix applies to "
                "already-missing data."
            )
        elif health_state == "unhealthy":
            what_happened = (
                f"SCM reports {count} container(s) with replicas in inconsistent "
                f"states ({metric_key}={count})."
            )
            why_it_happened = (
                "A datanode likely crashed mid-close or a network partition "
                "occurred during a Ratis pipeline transition, leaving replicas "
                "disagreeing on container state."
            )
            how_to_fix = (
                "Inspect the affected containers with `ozone admin container "
                "info <id>` to determine the authoritative replica before any "
                "repair. No automated config fix applies."
            )
        else:
            what_happened = (
                f"SCM reports {count} under-replicated container(s) "
                f"({metric_key}={count})."
            )
            why_it_happened = (
                "ReplicationManager's under-replicated queue is not draining "
                "fast enough for the current backlog, or there are not enough "
                "healthy target datanodes for the container's placement policy."
            )
            prop = "hdds.scm.replication.under.replicated.interval"
            current_interval = config_properties.get(prop, "30s")
            proposed_interval = self._halve_duration(current_interval)
            how_to_fix = (
                f"Halve {prop} from {current_interval} to {proposed_interval} so "
                "ReplicationManager re-checks the queue more often. Also verify "
                "datanode capacity and placement-policy constraints."
            )
            recommended_fix = {
                "action_id": "increase_under_replicated_queue_processing_frequency",
                "summary": "Process the under-replicated container queue more often.",
                "config_changes": {prop: proposed_interval},
                "rationale": (
                    "Halving the check interval lets ReplicationManager react to "
                    "queue changes sooner when datanodes are healthy."
                ),
            }

        return json.dumps(
            {
                "what_happened": what_happened,
                "why_it_happened": why_it_happened,
                "how_to_fix": how_to_fix,
                "evidence": evidence,
                "recommended_fix": recommended_fix,
            }
        )


class OpenAICompatibleLLMClient(LLMClient):
    """Talks to any OpenAI chat-completions-compatible endpoint
    (self-hosted vLLM/Ollama gateway, Azure OpenAI, OpenAI itself, ...)."""

    def generate(self, prompt: str) -> str:
        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        # Never log the Authorization header -- it carries settings.llm_api_key.
        logger.info(
            "OpenAICompatibleLLMClient.generate: POST %s model=%s prompt_chars=%d",
            url, settings.llm_model, len(prompt),
        )
        logger.debug("OpenAICompatibleLLMClient.generate: prompt=%s", prompt)
        response = httpx.post(
            url,
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
        content = response.json()["choices"][0]["message"]["content"]
        logger.info("OpenAICompatibleLLMClient.generate: reply=%s", content)
        return content


def get_llm_client() -> LLMClient:
    if settings.llm_base_url and settings.llm_api_key:
        logger.info(
            "get_llm_client: selecting OpenAICompatibleLLMClient (base_url=%s model=%s)",
            settings.llm_base_url, settings.llm_model,
        )
        return OpenAICompatibleLLMClient()
    logger.info("get_llm_client: no llm_base_url/llm_api_key configured -- selecting MockLLMClient")
    return MockLLMClient()
