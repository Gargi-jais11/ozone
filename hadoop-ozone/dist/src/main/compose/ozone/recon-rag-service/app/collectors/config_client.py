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

import json
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, Optional

import httpx

from app.config import settings


class ConfigFetchError(RuntimeError):
    """Raised when the target service's /conf endpoint can't be reached or
    parsed."""


def _parse_json_properties(body: str) -> Dict[str, str]:
    payload = json.loads(body)
    properties = payload.get("properties", [])
    return {
        prop["key"]: prop.get("value", "")
        for prop in properties
        if prop.get("key")
    }


def _parse_xml_properties(body: str) -> Dict[str, str]:
    root = ET.fromstring(body)
    parsed: Dict[str, str] = {}
    for prop in root.findall(".//property"):
        name_el = prop.find("name")
        value_el = prop.find("value")
        if name_el is not None and name_el.text and value_el is not None:
            parsed[name_el.text.strip()] = (value_el.text or "").strip()
    return parsed


def _load_properties(body: str, expect_json: bool) -> Dict[str, str]:
    if not body:
        return {}
    if expect_json:
        try:
            return _parse_json_properties(body)
        except ValueError:
            return _parse_xml_properties(body)
    return _parse_xml_properties(body)


def fetch_properties(http_address: str, property_names: Iterable[str]) -> Dict[str, str]:
    """Fetch ``http://<http_address>/conf`` and return only the requested
    property names (missing ones are simply omitted).

    Tries ``format=json`` first, then falls back to the default XML response.
    """

    wanted = set(property_names)
    url = f"http://{http_address}/conf"
    last_error: Optional[Exception] = None

    for params in ({"format": "json"}, None):
        try:
            response = httpx.get(
                url, params=params, timeout=settings.http_client_timeout_seconds
            )
            response.raise_for_status()
            all_properties = _load_properties(
                response.text.strip(), expect_json=params is not None
            )
            return {key: value for key, value in all_properties.items() if key in wanted}
        except httpx.HTTPError as exc:
            last_error = exc
        except ET.ParseError as exc:
            last_error = exc

    raise ConfigFetchError(f"Failed to fetch config from {url}: {last_error}") from last_error
