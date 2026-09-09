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

import httpx
import pytest
import respx
from httpx import Response

from app.remediation import docker_executor
from app.remediation.docker_executor import DockerExecutionError

CONTAINER = "om"


@respx.mock
def test_exec_in_container_returns_output_on_success():
    respx.post("http://docker/containers/om/exec").mock(
        return_value=Response(201, json={"Id": "exec-1"})
    )
    respx.post("http://docker/exec/exec-1/start").mock(return_value=Response(200, text="ok\n"))
    respx.get("http://docker/exec/exec-1/json").mock(
        return_value=Response(200, json={"ExitCode": 0})
    )

    output = docker_executor.exec_in_container(CONTAINER, ["echo", "ok"])

    assert output == "ok\n"


@respx.mock
def test_exec_in_container_raises_on_nonzero_exit_code():
    respx.post("http://docker/containers/om/exec").mock(
        return_value=Response(201, json={"Id": "exec-2"})
    )
    respx.post("http://docker/exec/exec-2/start").mock(return_value=Response(200, text="boom\n"))
    respx.get("http://docker/exec/exec-2/json").mock(
        return_value=Response(200, json={"ExitCode": 1})
    )

    with pytest.raises(DockerExecutionError, match="exited with code 1"):
        docker_executor.exec_in_container(CONTAINER, ["false"])


@respx.mock
def test_exec_in_container_raises_on_docker_api_error():
    respx.post("http://docker/containers/om/exec").mock(
        return_value=Response(404, json={"message": "No such container: om"})
    )

    with pytest.raises(DockerExecutionError, match="Docker Engine API call failed"):
        docker_executor.exec_in_container(CONTAINER, ["echo", "hi"])


@respx.mock
def test_restart_container_success():
    respx.post("http://docker/containers/om/restart").mock(return_value=Response(204))

    docker_executor.restart_container(CONTAINER)


@respx.mock
def test_restart_container_raises_on_error():
    respx.post("http://docker/containers/om/restart").mock(
        return_value=Response(500, json={"message": "restart failed"})
    )

    with pytest.raises(DockerExecutionError, match="Failed to restart container"):
        docker_executor.restart_container(CONTAINER)


@respx.mock
def test_set_config_property_runs_python_script_via_exec(monkeypatch):
    captured = {}

    def fake_exec_in_container(container, cmd):
        captured["container"] = container
        captured["cmd"] = cmd
        return "set ok\n"

    monkeypatch.setattr(docker_executor, "exec_in_container", fake_exec_in_container)

    output = docker_executor.set_config_property(
        "om", "/etc/hadoop/ozone-site.xml", "ozone.key.deleting.limit.per.task", "100000"
    )

    assert output == "set ok\n"
    assert captured["container"] == "om"
    assert captured["cmd"][0:2] == ["python3", "-c"]
    script = captured["cmd"][2]
    assert "ozone.key.deleting.limit.per.task" in script
    assert "100000" in script
    assert "/etc/hadoop/ozone-site.xml" in script
