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
"""Thin client for the standard Hadoop JMXJsonServlet exposed by every Ozone
HTTP server (OM, SCM, Recon, Datanode) at ``/jmx``.
"""

from typing import Any, Dict, List, Optional

import httpx

from app.config import settings


class JmxFetchError(RuntimeError):
    """Raised when the target service's /jmx endpoint can't be reached or
    parsed. Kept as a distinct type so callers can turn it into a diagnosis
    note instead of a 500."""


def fetch_beans(http_address: str, qry: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the ``beans`` array from ``http://<http_address>/jmx``,
    optionally filtered server-side to a single MBean name via ``qry``
    (e.g. ``Hadoop:service=OzoneManager,name=DeletingServiceMetrics``).
    """

    url = f"http://{http_address}/jmx"
    params = {"qry": qry} if qry else None
    try:
        response = httpx.get(url, params=params, timeout=settings.http_client_timeout_seconds)
        response.raise_for_status()
        return response.json().get("beans", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise JmxFetchError(f"Failed to fetch JMX beans from {url} (qry={qry}): {exc}") from exc


def fetch_bean(http_address: str, qry: str) -> Dict[str, Any]:
    """Convenience wrapper for the common case of expecting exactly one bean
    for ``qry``. Returns an empty dict (rather than raising) if the bean is
    absent, since "the metric doesn't exist" is itself diagnostic evidence."""

    return merge_bean_attributes(fetch_beans(http_address, qry=qry))


def merge_bean_attributes(beans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge attribute dicts from every JMX bean in ``beans``.

    SCMBlockDeletingServiceMetrics publishes counters on the main bean and
    backlog gauges on additional records with the same ``name=`` query; merging
    gives the plugin one flat metrics map.
    """

    merged: Dict[str, Any] = {}
    for bean in beans:
        for key, value in bean.items():
            if key in ("name", "modelerType"):
                continue
            merged[key] = value
    return merged
