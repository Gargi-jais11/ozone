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
"""In-process plugin registry.

This is the Python analogue of the ServiceLoader-based SPI pattern already
used elsewhere in Ozone (see OmTransportFactory.createFactory in
hadoop-ozone/common): a single well-known extension point (alert_type ->
plugin) that new plugin modules attach themselves to via a decorator at
import time, instead of a central if/elif dispatch.
"""

from typing import Dict, List, Type

from app.plugins.base import AlertDiagnosticPlugin

_REGISTRY: Dict[str, AlertDiagnosticPlugin] = {}


def register_plugin(
    *extra_alert_types: str,
) -> Type[AlertDiagnosticPlugin]:
    """Class decorator: registers ``plugin_cls`` under its ``alert_type`` and
    any additional Prometheus ``alertname`` values listed in
    ``extra_alert_types``."""

    def decorator(plugin_cls: Type[AlertDiagnosticPlugin]) -> Type[AlertDiagnosticPlugin]:
        instance = plugin_cls()
        alert_types = dict.fromkeys((instance.alert_type,) + extra_alert_types)
        for alert_type in alert_types:
            if alert_type in _REGISTRY:
                raise ValueError(
                    f"Duplicate plugin registration for alert_type={alert_type!r}"
                )
            _REGISTRY[alert_type] = instance
        return plugin_cls

    return decorator


def get_plugin(alert_type: str) -> AlertDiagnosticPlugin:
    try:
        return _REGISTRY[alert_type]
    except KeyError:
        raise KeyError(
            f"No diagnostic plugin registered for alert_type={alert_type!r}. "
            f"Known types: {sorted(_REGISTRY)}"
        ) from None


def list_plugins() -> List[str]:
    return sorted(_REGISTRY)
