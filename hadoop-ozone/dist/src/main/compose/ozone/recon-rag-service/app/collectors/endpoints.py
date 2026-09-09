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
"""Resolve which Ozone HTTP endpoint to query for a Prometheus alert."""

from typing import Optional, Tuple

from app.config import ClusterEndpoints
from app.models import AlertPayload

COMPONENT_OM = "om"
COMPONENT_SCM = "scm"
COMPONENT_DATANODE = "datanode"

ALERT_NAME_TO_COMPONENT = {
    "OzoneOmDeletionNotProgressing": COMPONENT_OM,
    "OzoneScmDeletionNotProgressing": COMPONENT_SCM,
    "OzoneDatanodeDeletionNotProgressing": COMPONENT_DATANODE,
    # Legacy single alertname (uses labels.component when present).
    "OzoneDeletionNotProgressing": COMPONENT_OM,
}


def deletion_component(alert: AlertPayload) -> str:
    """Return om, scm, or datanode from alertname or labels.component."""

    alert_name = alert.alert_type
    if alert_name in ALERT_NAME_TO_COMPONENT:
        mapped = ALERT_NAME_TO_COMPONENT[alert_name]
        if alert_name != "OzoneDeletionNotProgressing":
            return mapped
        component = alert.labels.get("component", mapped).lower()
        if component in (COMPONENT_OM, COMPONENT_SCM, COMPONENT_DATANODE):
            return component
        return mapped

    component = alert.labels.get("component", COMPONENT_OM).lower()
    if component in (COMPONENT_OM, COMPONENT_SCM, COMPONENT_DATANODE):
        return component
    return COMPONENT_OM


def resolve_http_address(
    alert: AlertPayload, cluster: ClusterEndpoints
) -> Tuple[str, Optional[str]]:
    """Map a firing alert to the host:port whose /jmx and /conf should be read.

    Returns (http_address, warning) where warning is set when the address had
    to be inferred rather than taken from ``labels.instance``.
    """

    component = deletion_component(alert)
    instance = alert.labels.get("instance", "").strip()

    if component == COMPONENT_OM:
        if instance:
            return instance, None
        return cluster.om_http_address, (
            "Alert labels did not include instance; using OM_HTTP_ADDRESS default."
        )

    if component == COMPONENT_SCM:
        if instance:
            return instance, None
        return cluster.scm_http_address, (
            "Alert labels did not include instance; using SCM_HTTP_ADDRESS default."
        )

    if instance:
        return instance, None
    return "", (
        "Datanode alert is missing labels.instance; cannot determine which "
        "datanode to query."
    )
