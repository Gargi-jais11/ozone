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
from app.plugins.scm_deletion_not_progressing import (
    INCREASE_SCM_BLOCK_DELETION_LIMIT,
    SCM_BLOCK_DELETING_JMX_QUERY,
    ScmDeletionNotProgressingPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
SCM_ALERT = AlertPayload(
    labels={"alertname": "OzoneScmDeletionNotProgressing", "component": "scm", "instance": "scm:9876"}
)


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

    context = ScmDeletionNotProgressingPlugin().collect_context(SCM_ALERT, CLUSTER)

    assert context.jmx_metrics["NumBlockDeletionTransactions"] == 42
    assert context.jmx_metrics["NumBlockDeletionTransactionCompleted"] == 10
    assert context.config_properties["hdds.scm.block.deletion.per-interval.max"] == "500000"
    assert any("Deletion hop under diagnosis: scm" in note for note in context.notes)


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

    context = ScmDeletionNotProgressingPlugin().collect_context(SCM_ALERT, CLUSTER)

    assert context.config_properties["hdds.scm.block.deletion.per-interval.max"] == "500000"


def test_permitted_actions_returns_single_scm_action():
    action_ids = {
        action.action_id for action in ScmDeletionNotProgressingPlugin().permitted_actions()
    }
    assert action_ids == {INCREASE_SCM_BLOCK_DELETION_LIMIT}


def test_permitted_actions_for_returns_single_scm_action():
    plugin = ScmDeletionNotProgressingPlugin()
    scm_actions = plugin.permitted_actions_for(DiagnosticContext(alert=SCM_ALERT))

    assert [action.action_id for action in scm_actions] == [INCREASE_SCM_BLOCK_DELETION_LIMIT]


def test_build_remediation_plan_doubles_scm_limit():
    context = DiagnosticContext(
        alert=SCM_ALERT,
        config_properties={"hdds.scm.block.deletion.per-interval.max": "500000"},
    )
    plan = ScmDeletionNotProgressingPlugin().build_remediation_plan(
        INCREASE_SCM_BLOCK_DELETION_LIMIT, context
    )

    assert plan.config_changes == {"hdds.scm.block.deletion.per-interval.max": "1000000"}
    assert plan.requires_restart is False


def test_build_remediation_plan_rejects_wrong_action():
    context = DiagnosticContext(alert=SCM_ALERT)
    with pytest.raises(ValueError, match="not valid for the SCM deletion plugin"):
        ScmDeletionNotProgressingPlugin().build_remediation_plan("some_other_action", context)


def test_evaluate_alert_no_metrics_is_unconfirmed():
    confirmed, reason = ScmDeletionNotProgressingPlugin().evaluate_alert(
        DiagnosticContext(alert=SCM_ALERT)
    )
    assert confirmed is False
    assert "not be collect" in reason.lower() or "could not collect" in reason.lower()


def test_evaluate_alert_scm_confirms_real_backlog():
    context = DiagnosticContext(
        alert=SCM_ALERT,
        jmx_metrics={"NumBlockDeletionTransactions": 42, "NumBlockDeletionTransactionCompleted": 10},
    )
    confirmed, reason = ScmDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is True


def test_evaluate_alert_scm_flags_likely_false_positive():
    context = DiagnosticContext(
        alert=SCM_ALERT,
        jmx_metrics={"NumBlockDeletionTransactions": 10, "NumBlockDeletionTransactionCompleted": 10},
    )
    confirmed, reason = ScmDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is False
