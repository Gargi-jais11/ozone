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
"""Runtime settings for the recon-rag-service, sourced entirely from env vars.

Kept as a single module (rather than a settings framework) so the service has
no required dependency beyond what's already in requirements.txt.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ClusterEndpoints:
    """host:port pairs for the Ozone services this container talks to."""

    om_http_address: str = os.environ.get("OM_HTTP_ADDRESS", "om:9874")
    scm_http_address: str = os.environ.get("SCM_HTTP_ADDRESS", "scm:9876")
    recon_http_address: str = os.environ.get("RECON_HTTP_ADDRESS", "recon:9888")


@dataclass(frozen=True)
class Settings:
    """Process-wide configuration, read once at import time."""

    cluster: ClusterEndpoints = field(default_factory=ClusterEndpoints)

    # RAG pipeline backend selection. Defaults keep the container dependency-light
    # and fully offline; both are swappable without code changes.
    vector_store_backend: str = os.environ.get("RAG_VECTOR_STORE_BACKEND", "memory")
    knowledge_base_dir: Path = Path(
        os.environ.get("RAG_KNOWLEDGE_BASE_DIR", "/app/knowledge_base")
    )
    chroma_persist_dir: str = os.environ.get("RAG_CHROMA_PERSIST_DIR", "/tmp/recon-rag-chroma")

    llm_base_url: str = os.environ.get("RAG_LLM_BASE_URL", "")
    llm_api_key: str = os.environ.get("RAG_LLM_API_KEY", "")
    llm_model: str = os.environ.get("RAG_LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds: float = float(os.environ.get("RAG_LLM_TIMEOUT_SECONDS", "30"))

    http_client_timeout_seconds: float = float(os.environ.get("RAG_HTTP_CLIENT_TIMEOUT_SECONDS", "10"))

    cors_allowed_origins: str = os.environ.get("RAG_CORS_ALLOWED_ORIGINS", "*")

    # Verbosity for the module-level loggers used across collectors/plugins/rag
    # (e.g. "DEBUG" to see full JMX/config payloads and LLM prompts/replies).
    log_level: str = os.environ.get("RAG_LOG_LEVEL", "INFO")

    # Safety switch: with this off (the default), /remediate only ever plans
    # remediations and never applies them. Flipping it on additionally
    # requires the caller to pass dryRun=false before executor.validate_and_plan
    # will touch the cluster via app.remediation.live_apply/docker_executor.
    allow_live_remediation: bool = _env_bool("RAG_ALLOW_LIVE_REMEDIATION", False)

    # Path to the Docker Engine API socket, mounted read-write into this
    # container so live remediation can edit a target container's config and
    # invoke `ozone admin reconfig`. Only ever dialed when a human has
    # confirmed an apply through the Recon UI AND allow_live_remediation=true.
    docker_socket_path: str = os.environ.get("RAG_DOCKER_SOCKET", "/var/run/docker.sock")

    # Docker container names as seen by `docker ps` (not compose hostnames).
    # Separate from OM_HTTP_ADDRESS etc. because the Docker Engine API
    # identifies containers by name/ID, while `ozone admin reconfig` uses the
    # service's network hostname (om/scm/datanode).
    om_docker_container: str = os.environ.get("OM_DOCKER_CONTAINER", "ozone-om-1")
    scm_docker_container: str = os.environ.get("SCM_DOCKER_CONTAINER", "ozone-scm-1")
    datanode_docker_container: str = os.environ.get("DATANODE_DOCKER_CONTAINER", "ozone-datanode-1")

    # Path to ozone-site.xml inside target service containers. Compose sets
    # OZONE_CONF_DIR=/etc/hadoop, so this must match -- not /opt/hadoop/etc/hadoop,
    # which is the source tree copy the entrypoint reads once at startup.
    ozone_site_xml_path: str = os.environ.get(
        "RAG_OZONE_SITE_XML_PATH", "/etc/hadoop/ozone-site.xml"
    )


settings = Settings()
