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
"""Thin client for the standard HddsConfServlet exposed by every Ozone HTTP
server at ``/conf``.

The docker-config file for the compose dev cluster already flags this
endpoint as potentially sensitive (it can echo back secrets configured in
ozone-site.xml), so this client always requests an explicit allowlist of
property names rather than ever dumping the full effective configuration.
"""

from typing import Dict, Iterable

import httpx

from app.config import settings


class ConfigFetchError(RuntimeError):
    """Raised when the target service's /conf endpoint can't be reached or
    parsed."""


def fetch_properties(http_address: str, property_names: Iterable[str]) -> Dict[str, str]:
    """Fetch ``http://<http_address>/conf?format=json`` and return only the
    requested property names (missing ones are simply omitted, not errored,
    since "not overridden" is a valid and common state)."""

    wanted = set(property_names)
    url = f"http://{http_address}/conf"
    try:
        response = httpx.get(
            url, params={"format": "json"}, timeout=settings.http_client_timeout_seconds
        )
        response.raise_for_status()
        properties = response.json().get("properties", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise ConfigFetchError(f"Failed to fetch config from {url}: {exc}") from exc

    return {
        prop["key"]: prop.get("value", "")
        for prop in properties
        if prop.get("key") in wanted
    }
