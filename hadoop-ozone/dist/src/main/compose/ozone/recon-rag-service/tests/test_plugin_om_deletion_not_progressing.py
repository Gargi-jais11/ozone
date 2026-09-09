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
from app.plugins.om_deletion_not_progressing import (
    INCREASE_KEY_DELETING_LIMIT,
    OM_DELETING_SERVICE_JMX_QUERY,
    OmDeletionNotProgressingPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
OM_ALERT = AlertPayload(
    labels={"alertname": "OzoneOmDeletionNotProgressing", "component": "om", "instance": "om:9874"}
)


RECON_SUMMARY_URL = "http://recon:9888/api/v1/keys/deletePending/summary"


def _mock_recon_summary(pending_keys: int) -> None:
    respx.get(RECON_SUMMARY_URL).mock(
        return_value=Response(200, json={"totalDeletedKeys": pending_keys})
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
    _mock_recon_summary(8)

    context = OmDeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics["numKeysProcessed"] == 100
    assert context.jmx_metrics["numKeysPurged"] == 20
    assert context.jmx_metrics["reconPendingDeleteKeys"] == 8
    assert context.config_properties == {"ozone.key.deleting.limit.per.task": "50000"}
    assert any("Deletion hop under diagnosis: om" in note for note in context.notes)
    assert any("no evidence of a scan-limit-caused backlog" in note for note in context.notes)


@respx.mock
def test_collect_context_missing_bean_adds_note():
    respx.get("http://om:9874/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))
    _mock_recon_summary(0)

    context = OmDeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics["reconPendingDeleteKeys"] == 0
    assert any("DeletingServiceMetrics MBean" in note for note in context.notes)


@respx.mock
def test_collect_context_jmx_unreachable_adds_note_not_raises():
    respx.get("http://om:9874/jmx").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))
    _mock_recon_summary(0)

    context = OmDeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert context.jmx_metrics["reconPendingDeleteKeys"] == 0
    assert any("Could not reach OM JMX endpoint" in note for note in context.notes)


@respx.mock
def test_collect_context_recon_unreachable_adds_note_not_raises():
    respx.get("http://om:9874/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://om:9874/conf").mock(return_value=Response(200, json={"properties": []}))
    respx.get(RECON_SUMMARY_URL).mock(side_effect=httpx.ConnectError("boom"))

    context = OmDeletionNotProgressingPlugin().collect_context(OM_ALERT, CLUSTER)

    assert "reconPendingDeleteKeys" not in context.jmx_metrics
    assert any("Could not reach Recon" in note for note in context.notes)


def test_permitted_actions_returns_single_om_action():
    action_ids = {action.action_id for action in OmDeletionNotProgressingPlugin().permitted_actions()}
    assert action_ids == {INCREASE_KEY_DELETING_LIMIT}


def test_permitted_actions_for_returns_single_om_action_when_backlog_exceeds_limit():
    plugin = OmDeletionNotProgressingPlugin()
    context = DiagnosticContext(
        alert=OM_ALERT,
        jmx_metrics={"reconPendingDeleteKeys": 60000},
        config_properties={"ozone.key.deleting.limit.per.task": "50000"},
    )
    om_actions = plugin.permitted_actions_for(context)

    assert [action.action_id for action in om_actions] == [INCREASE_KEY_DELETING_LIMIT]


def test_permitted_actions_for_returns_empty_without_backlog_evidence():
    plugin = OmDeletionNotProgressingPlugin()
    om_actions = plugin.permitted_actions_for(DiagnosticContext(alert=OM_ALERT))

    assert om_actions == []


def test_permitted_actions_for_returns_empty_when_pending_at_or_below_limit():
    plugin = OmDeletionNotProgressingPlugin()
    context = DiagnosticContext(
        alert=OM_ALERT,
        jmx_metrics={"reconPendingDeleteKeys": 50000},
        config_properties={"ozone.key.deleting.limit.per.task": "50000"},
    )
    om_actions = plugin.permitted_actions_for(context)

    assert om_actions == []


def test_build_remediation_plan_doubles_current_om_limit():
    context = DiagnosticContext(
        alert=OM_ALERT,
        config_properties={"ozone.key.deleting.limit.per.task": "50000"},
    )
    plan = OmDeletionNotProgressingPlugin().build_remediation_plan(INCREASE_KEY_DELETING_LIMIT, context)

    assert plan.config_changes == {"ozone.key.deleting.limit.per.task": "100000"}
    assert plan.dry_run is True
    assert plan.applied is False
    assert plan.requires_restart is True


def test_build_remediation_plan_rejects_wrong_action():
    context = DiagnosticContext(alert=OM_ALERT)
    with pytest.raises(ValueError, match="not valid for the OM deletion plugin"):
        OmDeletionNotProgressingPlugin().build_remediation_plan("some_other_action", context)


def test_evaluate_alert_no_metrics_is_unconfirmed():
    confirmed, reason = OmDeletionNotProgressingPlugin().evaluate_alert(
        DiagnosticContext(alert=OM_ALERT)
    )
    assert confirmed is False
    assert "not be collect" in reason.lower() or "could not collect" in reason.lower()


def test_evaluate_alert_om_confirms_real_backlog():
    context = DiagnosticContext(
        alert=OM_ALERT,
        jmx_metrics={"numKeysProcessed": 100, "numKeysPurged": 20},
    )
    confirmed, reason = OmDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is True
    assert "80" in reason


def test_evaluate_alert_om_flags_likely_false_positive():
    context = DiagnosticContext(
        alert=OM_ALERT,
        jmx_metrics={"numKeysProcessed": 100, "numKeysPurged": 100},
    )
    confirmed, reason = OmDeletionNotProgressingPlugin().evaluate_alert(context)
    assert confirmed is False
    assert "stale" in reason.lower() or "resolved" in reason.lower()
