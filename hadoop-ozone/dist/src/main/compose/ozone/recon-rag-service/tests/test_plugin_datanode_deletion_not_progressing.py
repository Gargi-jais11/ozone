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
import respx
from httpx import Response

from app.config import ClusterEndpoints
from app.models import AlertPayload, DiagnosticContext
from app.plugins.datanode_deletion_not_progressing import (
    DECREASE_DN_BLOCK_DELETING_INTERVAL,
    DN_BLOCK_DELETING_JMX_QUERY,
    DatanodeDeletionNotProgressingPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
DN_ALERT = AlertPayload(
    labels={
        "alertname": "OzoneDatanodeDeletionNotProgressing",
        "component": "datanode",
        "instance": "ozone-datanode-1:9882",
    }
)


@respx.mock
def test_collect_context_datanode_happy_path():
    respx.get("http://ozone-datanode-1:9882/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "name": DN_BLOCK_DELETING_JMX_QUERY,
                        "TotalPendingBlockCount": 15,
                        "SuccessCount": 0,
                    }
                ]
            },
        )
    )
    respx.get("http://ozone-datanode-1:9882/conf").mock(
        return_value=Response(
            200,
            json={
                "properties": [
                    {"key": "ozone.block.deleting.service.interval", "value": "60s"},
                ]
            },
        )
    )

    context = DatanodeDeletionNotProgressingPlugin().collect_context(DN_ALERT, CLUSTER)

    assert context.jmx_metrics["TotalPendingBlockCount"] == 15
    assert context.config_properties["ozone.block.deleting.service.interval"] == "60s"
    assert any("Deletion hop under diagnosis: datanode" in note for note in context.notes)


def test_permitted_actions_returns_single_dn_action():
    action_ids = {
        action.action_id for action in DatanodeDeletionNotProgressingPlugin().permitted_actions()
    }
    assert action_ids == {DECREASE_DN_BLOCK_DELETING_INTERVAL}


def test_permitted_actions_for_returns_single_dn_action():
    plugin = DatanodeDeletionNotProgressingPlugin()
    dn_actions = plugin.permitted_actions_for(DiagnosticContext(alert=DN_ALERT))

    assert [action.action_id for action in dn_actions] == [DECREASE_DN_BLOCK_DELETING_INTERVAL]


def test_build_remediation_plan_halves_datanode_interval():
    context = DiagnosticContext(
        alert=DN_ALERT,
        config_properties={"ozone.block.deleting.service.interval": "60s"},
    )
    plan = DatanodeDeletionNotProgressingPlugin().build_remediation_plan(
        DECREASE_DN_BLOCK_DELETING_INTERVAL, context
    )

    assert plan.config_changes == {"ozone.block.deleting.service.interval": "30s"}
    assert plan.requires_restart is False


def test_build_remediation_plan_rejects_wrong_action():
    context = DiagnosticContext(alert=DN_ALERT)
    with pytest.raises(ValueError, match="not valid for the datanode deletion"):
        DatanodeDeletionNotProgressingPlugin().build_remediation_plan("some_other_action", context)


def test_evaluate_alert_no_metrics_is_unconfirmed():
    confirmed, reason = DatanodeDeletionNotProgressingPlugin().evaluate_alert(
        DiagnosticContext(alert=DN_ALERT)
    )
    assert confirmed is False
    assert "not be collect" in reason.lower() or "could not collect" in reason.lower()


def test_evaluate_alert_datanode_confirms_real_backlog():
    context = DiagnosticContext(
        alert=DN_ALERT,
        jmx_metrics={"TotalPendingBlockCount": 15},
    )
    confirmed, reason = DatanodeDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is True


def test_evaluate_alert_datanode_flags_likely_false_positive():
    context = DiagnosticContext(
        alert=DN_ALERT,
        jmx_metrics={"TotalPendingBlockCount": 0},
    )
    confirmed, reason = DatanodeDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is False
