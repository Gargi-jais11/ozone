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

import logging
import re
from typing import Optional, Tuple

from app.config import ClusterEndpoints
from app.models import AlertPayload

logger = logging.getLogger(__name__)

COMPONENT_OM = "om"
COMPONENT_SCM = "scm"
COMPONENT_DATANODE = "datanode"

# labels.instance comes from the alert payload, not a trusted config file --
# collect_context ultimately interpolates it into an outbound HTTP request
# (jmx_client/config_client build f"http://{http_address}/..."), so a scheme,
# credentials, or path in this value would let a crafted/misconfigured alert
# steer that request (SSRF-shaped input). Only a bare host:port is accepted.
_HOST_PORT_RE = re.compile(r"^[A-Za-z0-9._-]+:[0-9]{1,5}$")


def _is_valid_host_port(value: str) -> bool:
    return bool(_HOST_PORT_RE.fullmatch(value))

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
    raw_instance = alert.labels.get("instance", "")
    instance = raw_instance.strip()
    rejected_note = None
    if instance and not _is_valid_host_port(instance):
        rejected_note = (
            f"Alert labels.instance={instance!r} was rejected as malformed "
            "(expected a bare host:port); ignoring it."
        )
        instance = ""

    logger.info(
        "resolve_http_address: component=%s raw_instance=%r accepted_instance=%r",
        component, raw_instance, instance or None,
    )

    if component == COMPONENT_OM:
        if instance:
            result = instance, None
        else:
            result = cluster.om_http_address, rejected_note or (
                "Alert labels did not include instance; using OM_HTTP_ADDRESS default."
            )
    elif component == COMPONENT_SCM:
        if instance:
            result = instance, None
        else:
            result = cluster.scm_http_address, rejected_note or (
                "Alert labels did not include instance; using SCM_HTTP_ADDRESS default."
            )
    elif instance:
        result = instance, None
    else:
        result = "", rejected_note or (
            "Datanode alert is missing labels.instance; cannot determine which "
            "datanode to query."
        )

    http_address, warning = result
    logger.info(
        "resolve_http_address: component=%s -> http_address=%r warning=%r",
        component, http_address, warning,
    )
    return result
