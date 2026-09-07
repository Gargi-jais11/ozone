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

from app.config import ClusterEndpoints
from app.models import AlertPayload, DiagnosticContext
from app.plugins.deletion_not_progressing import (
    DELETING_SERVICE_JMX_QUERY,
    INCREASE_KEY_DELETING_LIMIT,
    DeletionNotProgressingPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
ALERT = AlertPayload(labels={"alertname": "OzoneDeletionNotProgressing"})


@respx.mock
def test_collect_context_happy_path():
    respx.get("http://om:9874/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "numKeysProcessed": 100,
                        "numKeysSentForPurge": 100,
                        "numKeysPurged": 20,
                        "numDirsSentForPurge": 0,
                        "numDirsPurged": 0,
                        "metricsResetTimeStamp": "2026-09-01T00:00:00Z",
                    }
                ]
            },
        )
    )
    respx.get("http://om:9874/conf").mock(
        return_value=Response(
            200,
            json={
                "properties": [
                    {"key": "ozone.key.deleting.limit.per.task", "value": "50000"},
                    {"key": "unrelated.property", "value": "should-be-filtered"},
                ]
            },
        )
    )

    context = DeletionNotProgressingPlugin().collect_context(ALERT, CLUSTER)

    assert context.jmx_metrics["numKeysProcessed"] == 100
    assert context.jmx_metrics["numKeysPurged"] == 20
    assert context.config_properties == {"ozone.key.deleting.limit.per.task": "50000"}
    assert context.notes == []


@respx.mock
def test_collect_context_missing_bean_adds_note():
    respx.get("http://om:9874/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))

    context = DeletionNotProgressingPlugin().collect_context(ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("DeletingServiceMetrics MBean" in note for note in context.notes)


@respx.mock
def test_collect_context_jmx_unreachable_adds_note_not_raises():
    respx.get("http://om:9874/jmx").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))

    context = DeletionNotProgressingPlugin().collect_context(ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("Could not reach OM JMX endpoint" in note for note in context.notes)


def test_permitted_actions_contains_only_the_documented_action():
    action_ids = {action.action_id for action in DeletionNotProgressingPlugin().permitted_actions()}
    assert action_ids == {INCREASE_KEY_DELETING_LIMIT}


def test_build_remediation_plan_doubles_current_limit():
    context = DiagnosticContext(
        alert=ALERT,
        config_properties={"ozone.key.deleting.limit.per.task": "50000"},
    )
    plan = DeletionNotProgressingPlugin().build_remediation_plan(INCREASE_KEY_DELETING_LIMIT, context)

    assert plan.config_changes == {"ozone.key.deleting.limit.per.task": "100000"}
    assert plan.dry_run is True
    assert plan.applied is False
    assert plan.requires_restart is True


def test_build_remediation_plan_rejects_unknown_action():
    context = DiagnosticContext(alert=ALERT)
    with pytest.raises(ValueError):
        DeletionNotProgressingPlugin().build_remediation_plan("not_a_real_action", context)
