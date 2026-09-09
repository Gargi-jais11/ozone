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
    DECREASE_DN_BLOCK_DELETING_INTERVAL,
    DN_BLOCK_DELETING_JMX_QUERY,
    INCREASE_KEY_DELETING_LIMIT,
    INCREASE_SCM_BLOCK_DELETION_LIMIT,
    OM_DELETING_SERVICE_JMX_QUERY,
    SCM_BLOCK_DELETING_JMX_QUERY,
    DeletionNotProgressingPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
OM_ALERT = AlertPayload(
    labels={"alertname": "OzoneOmDeletionNotProgressing", "component": "om", "instance": "om:9874"}
)
SCM_ALERT = AlertPayload(
    labels={"alertname": "OzoneScmDeletionNotProgressing", "component": "scm", "instance": "scm:9876"}
)
DN_ALERT = AlertPayload(
    labels={
        "alertname": "OzoneDatanodeDeletionNotProgressing",
        "component": "datanode",
        "instance": "ozone-datanode-1:9882",
    }
)


@respx.mock
def test_collect_context_om_happy_path():
    respx.get("http://om:9874/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "name": OM_DELETING_SERVICE_JMX_QUERY,
                        "numKeysProcessed": 100,
                        "numKeysSentForPurge": 100,
                        "numKeysPurged": 20,
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
                ]
            },
        )
    )

    context = DeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics["numKeysProcessed"] == 100
    assert context.jmx_metrics["numKeysPurged"] == 20
    assert context.config_properties == {"ozone.key.deleting.limit.per.task": "50000"}
    assert any("Deletion hop under diagnosis: om" in note for note in context.notes)


@respx.mock
def test_collect_context_scm_happy_path():
    respx.get("http://scm:9876/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "name": SCM_BLOCK_DELETING_JMX_QUERY,
                        "NumBlockDeletionTransactionCompleted": 10,
                    },
                    {
                        "name": SCM_BLOCK_DELETING_JMX_QUERY,
                        "NumBlockDeletionTransactions": 42,
                    },
                ]
            },
        )
    )
    respx.get("http://scm:9876/conf").mock(
        return_value=Response(
            200,
            json={
                "properties": [
                    {"key": "hdds.scm.block.deletion.per-interval.max", "value": "500000"},
                ]
            },
        )
    )

    context = DeletionNotProgressingPlugin().collect_context(SCM_ALERT, CLUSTER)

    assert context.jmx_metrics["NumBlockDeletionTransactions"] == 42
    assert context.jmx_metrics["NumBlockDeletionTransactionCompleted"] == 10
    assert context.config_properties["hdds.scm.block.deletion.per-interval.max"] == "500000"
    assert any("Deletion hop under diagnosis: scm" in note for note in context.notes)


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

    context = DeletionNotProgressingPlugin().collect_context(DN_ALERT, CLUSTER)

    assert context.jmx_metrics["TotalPendingBlockCount"] == 15
    assert context.config_properties["ozone.block.deleting.service.interval"] == "60s"
    assert any("Deletion hop under diagnosis: datanode" in note for note in context.notes)


@respx.mock
def test_collect_context_missing_bean_adds_note():
    respx.get("http://om:9874/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))

    context = DeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("DeletingServiceMetrics MBean" in note for note in context.notes)


@respx.mock
def test_collect_context_jmx_unreachable_adds_note_not_raises():
    respx.get("http://om:9874/jmx").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))

    context = DeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("Could not reach OM JMX endpoint" in note for note in context.notes)


@respx.mock
def test_collect_context_parses_xml_conf_fallback():
    respx.get("http://scm:9876/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://scm:9876/conf").mock(
        return_value=Response(
            200,
            text=(
                "<configuration><property>"
                "<name>hdds.scm.block.deletion.per-interval.max</name>"
                "<value>500000</value>"
                "</property></configuration>"
            ),
        )
    )

    context = DeletionNotProgressingPlugin().collect_context(SCM_ALERT, CLUSTER)

    assert context.config_properties["hdds.scm.block.deletion.per-interval.max"] == "500000"


def test_permitted_actions_contains_all_hop_actions():
    action_ids = {action.action_id for action in DeletionNotProgressingPlugin().permitted_actions()}
    assert action_ids == {
        INCREASE_KEY_DELETING_LIMIT,
        INCREASE_SCM_BLOCK_DELETION_LIMIT,
        DECREASE_DN_BLOCK_DELETING_INTERVAL,
    }


def test_permitted_actions_for_filters_by_component():
    plugin = DeletionNotProgressingPlugin()
    om_actions = plugin.permitted_actions_for(DiagnosticContext(alert=OM_ALERT))
    scm_actions = plugin.permitted_actions_for(DiagnosticContext(alert=SCM_ALERT))
    dn_actions = plugin.permitted_actions_for(DiagnosticContext(alert=DN_ALERT))

    assert [action.action_id for action in om_actions] == [INCREASE_KEY_DELETING_LIMIT]
    assert [action.action_id for action in scm_actions] == [INCREASE_SCM_BLOCK_DELETION_LIMIT]
    assert [action.action_id for action in dn_actions] == [DECREASE_DN_BLOCK_DELETING_INTERVAL]


def test_build_remediation_plan_doubles_current_om_limit():
    context = DiagnosticContext(
        alert=OM_ALERT,
        config_properties={"ozone.key.deleting.limit.per.task": "50000"},
    )
    plan = DeletionNotProgressingPlugin().build_remediation_plan(INCREASE_KEY_DELETING_LIMIT, context)

    assert plan.config_changes == {"ozone.key.deleting.limit.per.task": "100000"}
    assert plan.dry_run is True
    assert plan.applied is False
    assert plan.requires_restart is True


def test_build_remediation_plan_doubles_scm_limit():
    context = DiagnosticContext(
        alert=SCM_ALERT,
        config_properties={"hdds.scm.block.deletion.per-interval.max": "500000"},
    )
    plan = DeletionNotProgressingPlugin().build_remediation_plan(
        INCREASE_SCM_BLOCK_DELETION_LIMIT, context
    )

    assert plan.config_changes == {"hdds.scm.block.deletion.per-interval.max": "1000000"}
    assert plan.requires_restart is False


def test_build_remediation_plan_halves_datanode_interval():
    context = DiagnosticContext(
        alert=DN_ALERT,
        config_properties={"ozone.block.deleting.service.interval": "60s"},
    )
    plan = DeletionNotProgressingPlugin().build_remediation_plan(
        DECREASE_DN_BLOCK_DELETING_INTERVAL, context
    )

    assert plan.config_changes == {"ozone.block.deleting.service.interval": "30s"}
    assert plan.requires_restart is False


def test_build_remediation_plan_rejects_wrong_action_for_component():
    context = DiagnosticContext(alert=SCM_ALERT)
    with pytest.raises(ValueError, match="not valid for deletion hop 'scm'"):
        DeletionNotProgressingPlugin().build_remediation_plan(INCREASE_KEY_DELETING_LIMIT, context)
