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

import pytest

from app.models import AlertPayload, DiagnosticContext, RemediationPlan
from app.remediation import live_apply
from app.remediation.docker_executor import DockerExecutionError


def _context(component: str, instance: str = "") -> DiagnosticContext:
    labels = {"component": component}
    if instance:
        labels["instance"] = instance
    return DiagnosticContext(alert=AlertPayload(labels=labels))


def _plan(**config_changes: str) -> RemediationPlan:
    return RemediationPlan(action_id="test_action", description="test", config_changes=config_changes)


def test_target_container_and_service_om():
    docker_container, reconfig_host, service, port = live_apply._target_container_and_service(
        _context("om")
    )
    assert (docker_container, reconfig_host, service, port) == ("ozone-om-1", "om", "OM", 9862)


def test_target_container_and_service_scm():
    docker_container, reconfig_host, service, port = live_apply._target_container_and_service(
        _context("scm")
    )
    assert (docker_container, reconfig_host, service, port) == ("ozone-scm-1", "scm", "SCM", 9860)


def test_target_container_and_service_datanode_uses_instance_host():
    docker_container, reconfig_host, service, port = live_apply._target_container_and_service(
        _context("datanode", instance="datanode2:9882")
    )
    assert (docker_container, reconfig_host, service, port) == (
        "ozone-datanode-1", "datanode2", "DATANODE", 19864,
    )


def test_target_container_and_service_datanode_defaults_without_instance():
    docker_container, reconfig_host, service, port = live_apply._target_container_and_service(
        _context("datanode")
    )
    assert (docker_container, reconfig_host, service, port) == (
        "ozone-datanode-1", "datanode", "DATANODE", 19864,
    )


def test_target_container_and_service_rejects_unknown_component():
    with pytest.raises(live_apply.LiveApplyError, match="Don't know how to apply"):
        live_apply._target_container_and_service(_context("s3g"))


def test_apply_plan_runs_set_then_reconfig(monkeypatch):
    calls = []

    def fake_set_config_property(container, path, name, value):
        calls.append(("set", container, path, name, value))
        return "set ok"

    def fake_get_config_property(container, path, name):
        calls.append(("get", container, path, name))
        return "100000"

    def fake_exec_in_container(container, cmd):
        calls.append(("exec", container, cmd))
        if cmd[-1] == "start":
            return "OM: Started reconfiguration task on node [om:9862]."
        return (
            "OM: Reconfiguring status for node [om:9862]: started at ... and finished at ...\n"
            "SUCCESS: Changed property ozone.key.deleting.limit.per.task\n"
            '\tFrom: "50000"\n\tTo: "100000"\n'
        )

    monkeypatch.setattr(live_apply.docker_executor, "set_config_property", fake_set_config_property)
    monkeypatch.setattr(live_apply.docker_executor, "get_config_property", fake_get_config_property)
    monkeypatch.setattr(live_apply.docker_executor, "exec_in_container", fake_exec_in_container)

    plan = _plan(**{"ozone.key.deleting.limit.per.task": "100000"})
    log = live_apply.apply_plan(_context("om"), plan)

    assert calls[0] == (
        "set", "ozone-om-1", "/etc/hadoop/ozone-site.xml", "ozone.key.deleting.limit.per.task", "100000",
    )
    assert calls[1] == (
        "get", "ozone-om-1", "/etc/hadoop/ozone-site.xml", "ozone.key.deleting.limit.per.task",
    )
    assert calls[2] == (
        "exec", "ozone-om-1",
        ["ozone", "admin", "reconfig", "--service", "OM", "--address", "om:9862", "start"],
    )
    assert calls[3] == (
        "exec", "ozone-om-1",
        ["ozone", "admin", "reconfig", "--service", "OM", "--address", "om:9862", "status"],
    )
    assert len(log) == 4
    assert "[1/2] Edited" in log[0]
    assert "[1/2] Verified" in log[1]
    assert "[2/2]" in log[2]
    assert "SUCCESS: Changed property ozone.key.deleting.limit.per.task" in log[3]


def test_apply_plan_raises_when_config_edit_does_not_stick(monkeypatch):
    monkeypatch.setattr(
        live_apply.docker_executor,
        "set_config_property",
        lambda container, path, name, value: "set ok",
    )
    monkeypatch.setattr(
        live_apply.docker_executor,
        "get_config_property",
        lambda container, path, name: "1",
    )

    plan = _plan(**{"ozone.key.deleting.limit.per.task": "500"})
    with pytest.raises(live_apply.LiveApplyError, match="Config edit did not stick"):
        live_apply.apply_plan(_context("om"), plan)


def test_apply_plan_raises_when_reconfig_applies_no_properties(monkeypatch):
    def fake_set_config_property(container, path, name, value):
        return "set ok"

    def fake_get_config_property(container, path, name):
        return "100000"

    def fake_exec_in_container(container, cmd):
        if cmd[-1] == "start":
            return "OM: Started reconfiguration task on node [om:9862]."
        return "OM: Reconfiguring status for node [om:9862]: started at ... and finished at ..."

    monkeypatch.setattr(live_apply.docker_executor, "set_config_property", fake_set_config_property)
    monkeypatch.setattr(live_apply.docker_executor, "get_config_property", fake_get_config_property)
    monkeypatch.setattr(live_apply.docker_executor, "exec_in_container", fake_exec_in_container)

    plan = _plan(**{"ozone.key.deleting.limit.per.task": "100000"})
    with pytest.raises(live_apply.LiveApplyError, match="no properties were applied"):
        live_apply.apply_plan(_context("om"), plan)


def test_apply_plan_raises_live_apply_error_on_docker_failure(monkeypatch):
    def failing_set_config_property(container, path, name, value):
        raise DockerExecutionError("boom")

    monkeypatch.setattr(live_apply.docker_executor, "set_config_property", failing_set_config_property)
    plan = _plan(**{"ozone.key.deleting.limit.per.task": "100000"})
    with pytest.raises(live_apply.LiveApplyError, match="boom"):
        live_apply.apply_plan(_context("om"), plan)
