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
"""Helpers shared by the OM/SCM/datanode deletion-not-progressing plugins.

Not itself a plugin -- app/plugins/__init__.py does not import this module for
side effects, only the three per-alertname plugin modules import it directly.
"""

import logging
import re
from typing import Dict, List

from app.collectors.config_client import ConfigFetchError, fetch_properties
from app.collectors.jmx_client import JmxFetchError, fetch_bean

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"(\d+)([smhd])")


def pick_metrics(bean: Dict[str, object], keys: tuple) -> Dict[str, object]:
    return {key: bean[key] for key in keys if key in bean}


def fetch_jmx_metrics(
    http_address: str,
    jmx_query: str,
    keys: tuple,
    notes: List[str],
    missing_bean_note: str,
    endpoint_label: str,
) -> Dict[str, object]:
    try:
        bean = fetch_bean(http_address, qry=jmx_query)
        if not bean:
            logger.warning(
                "%s: no bean returned for qry=%s at %s -- %s",
                endpoint_label, jmx_query, http_address, missing_bean_note,
            )
            notes.append(missing_bean_note)
            return {}
        picked = pick_metrics(bean, keys)
        logger.info(
            "%s: picked JMX metrics %s (of keys=%s) from bean=%s",
            endpoint_label, picked, keys, bean,
        )
        return picked
    except JmxFetchError as exc:
        logger.warning("%s: JMX fetch failed: %s", endpoint_label, exc)
        notes.append(f"Could not reach {endpoint_label} JMX endpoint: {exc}")
        return {}


def fetch_config_properties(
    http_address: str,
    property_names: tuple,
    notes: List[str],
    endpoint_label: str,
) -> Dict[str, str]:
    try:
        properties = fetch_properties(http_address, property_names)
        logger.info(
            "%s: fetched config properties %s (of requested=%s)",
            endpoint_label, properties, property_names,
        )
        return properties
    except ConfigFetchError as exc:
        logger.warning("%s: config fetch failed: %s", endpoint_label, exc)
        notes.append(f"Could not reach {endpoint_label} config endpoint: {exc}")
        return {}


def halve_duration(value: str) -> str:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        return value
    amount = int(match.group(1))
    unit = match.group(2)
    halved = max(1, amount // 2)
    return f"{halved}{unit}"
