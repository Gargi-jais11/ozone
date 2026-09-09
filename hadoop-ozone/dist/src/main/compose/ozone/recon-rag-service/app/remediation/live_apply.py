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
"""Applies a validated RemediationPlan to the real cluster.

Every property this build ships a fix for is on Ozone's documented list of
dynamically reconfigurable properties (see
hadoop-hdds/docs/content/feature/Reconfigurability.md), so applying a plan
always follows the same two-step, no-restart sequence:

  1. Patch the property's value directly in the target container's
     ozone-site.xml (docker_executor.set_config_property).
  2. Ask the service to reload it live: `ozone admin reconfig --service=...
     --address=... start`, then poll `status` until each property in the plan
     reports SUCCESS (docker_executor.exec_in_container).

This intentionally never calls docker_executor.restart_container: the
Ozone runner image's entrypoint regenerates ozone-site.xml from
OZONE-SITE.XML_* environment variables on every container start, so a
restart would discard the edit from step 1 instead of applying it.
RemediationPlan.requires_restart is therefore informational only in this
build -- restart-based execution is not wired up for any shipped action.
"""

import logging
import time
from typing import List, Tuple

from app.config import settings
from app.models import DiagnosticContext, RemediationPlan
from app.remediation import docker_executor

logger = logging.getLogger(__name__)

RECONFIG_STATUS_POLL_INTERVAL_SECONDS = 0.5
RECONFIG_STATUS_TIMEOUT_SECONDS = 30

# alert labels.component -> (docker container name, reconfig network hostname,
# `ozone admin reconfig --service` value, RPC port).
_COMPONENT_RECONFIG = {
    "om": (settings.om_docker_container, "om", "OM", 9862),
    "scm": (settings.scm_docker_container, "scm", "SCM", 9860),
}
DATANODE_RECONFIG_PORT = 19864


class LiveApplyError(RuntimeError):
    """A remediation plan could not be applied to the live cluster."""


def _target_container_and_service(context: DiagnosticContext) -> Tuple[str, str, str, int]:
    component = context.alert.labels.get("component", "").lower()
    if component in _COMPONENT_RECONFIG:
        return _COMPONENT_RECONFIG[component]
    if component == "datanode":
        instance = context.alert.labels.get("instance", "")
        reconfig_host = instance.split(":", 1)[0] if instance else "datanode"
        return settings.datanode_docker_container, reconfig_host, "DATANODE", DATANODE_RECONFIG_PORT
    raise LiveApplyError(f"Don't know how to apply a live fix for component={component!r}")


def _run_reconfig_and_verify(
    docker_container: str,
    service: str,
    address: str,
    property_names: List[str],
    config_path: str,
    log: List[str],
) -> None:
    """Start reconfiguration, poll until finished, and confirm every property
    in ``property_names`` was applied."""

    output = docker_executor.exec_in_container(
        docker_container,
        ["ozone", "admin", "reconfig", "--service", service, "--address", address, "start"],
    )
    log.append(
        f"[2/2] ozone admin reconfig --service={service} --address={address} start: "
        f"{output.strip()}"
    )

    deadline = time.monotonic() + RECONFIG_STATUS_TIMEOUT_SECONDS
    status_output = ""
    while time.monotonic() < deadline:
        status_output = docker_executor.exec_in_container(
            docker_container,
            ["ozone", "admin", "reconfig", "--service", service, "--address", address, "status"],
        )
        if "still running" in status_output:
            time.sleep(RECONFIG_STATUS_POLL_INTERVAL_SECONDS)
            continue
        if "finished" in status_output:
            log.append(
                f"[2/2] ozone admin reconfig --service={service} --address={address} status: "
                f"{status_output.strip()}"
            )
            break
        if "no task was found" in status_output:
            time.sleep(RECONFIG_STATUS_POLL_INTERVAL_SECONDS)
            continue
        time.sleep(RECONFIG_STATUS_POLL_INTERVAL_SECONDS)
    else:
        raise LiveApplyError(
            f"Reconfiguration on {address} did not finish within "
            f"{RECONFIG_STATUS_TIMEOUT_SECONDS}s. Last status: {status_output.strip()}"
        )

    if "SUCCESS: Changed property" not in status_output:
        raise LiveApplyError(
            f"Reconfiguration on {address} finished but no properties were applied. "
            f"Ensure {config_path} is the live config file (OZONE_CONF_DIR, typically "
            f"/etc/hadoop/ozone-site.xml in compose). Status: {status_output.strip()}"
        )

    for name in property_names:
        if f"Changed property {name}" not in status_output:
            raise LiveApplyError(
                f"Reconfiguration on {address} did not apply {name!r}. "
                f"Status: {status_output.strip()}"
            )


def apply_plan(context: DiagnosticContext, plan: RemediationPlan) -> List[str]:
    """Apply every property in ``plan.config_changes`` to the live cluster
    and return a human-readable log of what ran. Raises LiveApplyError on
    the first failure; callers should surface a partially-applied plan as
    failed rather than retry it blindly."""

    docker_container, reconfig_host, service, port = _target_container_and_service(context)
    address = f"{reconfig_host}:{port}"
    config_path = settings.ozone_site_xml_path
    log: List[str] = []
    property_names = list(plan.config_changes.keys())

    try:
        for name, value in plan.config_changes.items():
            # Step 1: patch ozone-site.xml on disk. Reconfig only reloads what is
            # already written here -- `ozone admin reconfig start` does not take
            # property names or values on the CLI.
            write_output = docker_executor.set_config_property(
                docker_container, config_path, name, value
            )
            log.append(
                f"[1/2] Edited {docker_container}:{config_path} -- "
                f"set {name}={value} ({write_output.strip()})"
            )
            actual = docker_executor.get_config_property(docker_container, config_path, name)
            if actual != str(value):
                raise LiveApplyError(
                    f"Config edit did not stick: {name!r} is {actual!r} in "
                    f"{config_path}, expected {value!r}. Check "
                    f"RAG_OZONE_SITE_XML_PATH (should be /etc/hadoop/ozone-site.xml "
                    f"in compose where OZONE_CONF_DIR=/etc/hadoop)."
                )
            log.append(
                f"[1/2] Verified {name}={actual} in {docker_container}:{config_path}"
            )

        _run_reconfig_and_verify(
            docker_container, service, address, property_names, config_path, log
        )
    except docker_executor.DockerExecutionError as exc:
        logger.error("live_apply failed for alert_type=%s: %s", context.alert.alert_type, exc)
        raise LiveApplyError(str(exc)) from exc

    return log
