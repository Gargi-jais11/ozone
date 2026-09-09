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
"""Thin client for the Docker Engine API, reached over the host's Docker
socket (mounted into this container -- see rag-service.yaml). This is the
only module in recon-rag-service that can mutate the cluster: it execs
commands inside a target container (om/scm/datanode) and can restart one.

Only ever called from app.remediation.live_apply, which is itself only
reached from executor.validate_and_plan when dryRun=false and
RAG_ALLOW_LIVE_REMEDIATION=true.
"""

import json
import logging
from typing import List

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class DockerExecutionError(RuntimeError):
    """A Docker Engine API call failed, or the command it ran inside the
    container exited with a non-zero status."""


def _client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=settings.docker_socket_path),
        base_url="http://docker",
        timeout=settings.http_client_timeout_seconds,
    )


def exec_in_container(container: str, cmd: List[str]) -> str:
    """Run ``cmd`` inside ``container`` via the Docker Engine API's exec
    endpoints and return its combined stdout/stderr. Raises
    DockerExecutionError if the API call fails or the command exits non-zero.
    """

    with _client() as client:
        try:
            create = client.post(
                f"/containers/{container}/exec",
                json={"Cmd": cmd, "AttachStdout": True, "AttachStderr": True, "Tty": True},
            )
            create.raise_for_status()
            exec_id = create.json()["Id"]

            start = client.post(f"/exec/{exec_id}/start", json={"Detach": False, "Tty": True})
            start.raise_for_status()
            output = start.text

            inspect = client.get(f"/exec/{exec_id}/json")
            inspect.raise_for_status()
            exit_code = inspect.json().get("ExitCode")
        except httpx.HTTPError as exc:
            raise DockerExecutionError(
                f"Docker Engine API call failed while running {cmd} in container {container!r}: {exc}"
            ) from exc

    logger.info("docker exec %s %s -> exit_code=%s output=%s", container, cmd, exit_code, output)
    if exit_code != 0:
        raise DockerExecutionError(
            f"Command {cmd} in container {container!r} exited with code {exit_code}: {output}"
        )
    return output


def restart_container(container: str, timeout_seconds: int = 10) -> None:
    """Restart ``container`` via the Docker Engine API. Not used by any
    shipped remediation action today (see live_apply module docstring for
    why config-only reconfiguration is used instead), but kept as a building
    block for future actions that genuinely require a restart."""

    with _client() as client:
        try:
            response = client.post(f"/containers/{container}/restart", params={"t": timeout_seconds})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DockerExecutionError(f"Failed to restart container {container!r}: {exc}") from exc
    logger.info("docker restart %s -> ok", container)


def set_config_property(container: str, config_path: str, name: str, value: str) -> str:
    """Add or update a single ``<property>`` in the Hadoop-style XML config
    file at ``config_path`` inside ``container``, using a small inline
    Python script (the Ozone runner image already ships python3, so this
    needs no extra tooling in the target container)."""

    script = (
        "import xml.etree.ElementTree as ET\n"
        f"path = {json.dumps(config_path)}\n"
        f"name = {json.dumps(name)}\n"
        f"value = {json.dumps(str(value))}\n"
        "tree = ET.parse(path)\n"
        "root = tree.getroot()\n"
        "prop = next((p for p in root.findall('property')\n"
        "             if p.find('name') is not None and p.find('name').text == name), None)\n"
        "if prop is None:\n"
        "    prop = ET.SubElement(root, 'property')\n"
        "    ET.SubElement(prop, 'name').text = name\n"
        "    ET.SubElement(prop, 'value').text = value\n"
        "else:\n"
        "    value_el = prop.find('value')\n"
        "    if value_el is None:\n"
        "        value_el = ET.SubElement(prop, 'value')\n"
        "    value_el.text = value\n"
        "tree.write(path, encoding='UTF-8', xml_declaration=True)\n"
        "print('set', name, '=', value, 'in', path)\n"
    )
    return exec_in_container(container, ["python3", "-c", script])


def get_config_property(container: str, config_path: str, name: str) -> str:
    """Read a single property value from a Hadoop-style XML config file inside
    ``container``. Raises DockerExecutionError if the file or property is
    missing."""

    script = (
        "import sys\n"
        "import xml.etree.ElementTree as ET\n"
        f"path = {json.dumps(config_path)}\n"
        f"name = {json.dumps(name)}\n"
        "tree = ET.parse(path)\n"
        "root = tree.getroot()\n"
        "prop = next((p for p in root.findall('property')\n"
        "             if p.find('name') is not None and p.find('name').text == name), None)\n"
        "if prop is None or prop.find('value') is None or prop.find('value').text is None:\n"
        "    sys.exit(1)\n"
        "print(prop.find('value').text)\n"
    )
    return exec_in_container(container, ["python3", "-c", script]).strip()
