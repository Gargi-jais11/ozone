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
"""Thin client for Recon's OM DB Insight REST API at ``/api/v1/keys/...``.

Unlike OM's own DeletingServiceMetrics JMX bean -- which only exposes
cumulative counters since the last metrics reset -- Recon's
``deletePending/summary`` endpoint reads the OM ``deletedTable`` directly, so
it reflects the live backlog size. That is what actually tells a plugin
whether a scan-limit config property is too small for the current backlog,
as opposed to a counter simply being non-zero.
"""

import logging
from typing import Any, Dict

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class ReconFetchError(RuntimeError):
    """Raised when Recon's REST API can't be reached or parsed."""


def fetch_deleted_key_summary(recon_http_address: str) -> Dict[str, Any]:
    """Return ``http://<recon_http_address>/api/v1/keys/deletePending/summary``.

    Example response: ``{"totalDeletedKeys": 8, "totalReplicatedDataSize":
    90000, "totalUnreplicatedDataSize": 30000}``.
    """

    url = f"http://{recon_http_address}/api/v1/keys/deletePending/summary"
    logger.info("GET %s", url)
    try:
        response = httpx.get(url, timeout=settings.http_client_timeout_seconds)
        response.raise_for_status()
        summary = response.json()
        logger.info("GET %s -> %s", url, summary)
        return summary
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GET %s failed: %s", url, exc)
        raise ReconFetchError(f"Failed to fetch deleted-key summary from {url}: {exc}") from exc
