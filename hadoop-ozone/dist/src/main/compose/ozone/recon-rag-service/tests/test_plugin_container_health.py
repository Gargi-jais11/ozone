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
from app.plugins.container_health import (
    INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY,
    REPLICATION_MANAGER_JMX_QUERY,
    ContainerHealthPlugin,
)

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)
MISSING_ALERT = AlertPayload(
    labels={"alertname": "OzoneScmContainerMissing", "component": "scm", "instance": "scm:9876"}
)
UNDER_REPLICATED_ALERT = AlertPayload(
    labels={
        "alertname": "OzoneScmContainerUnderReplicated",
        "component": "scm",
        "instance": "scm:9876",
    }
)
UNHEALTHY_ALERT = AlertPayload(
    labels={"alertname": "OzoneScmContainerUnhealthy", "component": "scm", "instance": "scm:9876"}
)


@respx.mock
def test_collect_context_happy_path():
    respx.get("http://scm:9876/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "name": REPLICATION_MANAGER_JMX_QUERY,
                        "UnderReplicatedContainers": 5,
                        "MissingContainers": 0,
                        "UnhealthyContainers": 0,
                        "UnderReplicatedQueueSize": 5,
                    }
                ]
            },
        )
    )
    respx.get("http://scm:9876/conf").mock(
        return_value=Response(
            200,
            json={
                "properties": [
                    {"key": "hdds.scm.replication.under.replicated.interval", "value": "30s"},
                ]
            },
        )
    )

    context = ContainerHealthPlugin().collect_context(UNDER_REPLICATED_ALERT, CLUSTER)

    assert context.jmx_metrics["UnderReplicatedContainers"] == 5
    assert context.config_properties["hdds.scm.replication.under.replicated.interval"] == "30s"
    assert any("Container health signal under diagnosis: under_replicated" in note for note in context.notes)


@respx.mock
def test_collect_context_missing_bean_adds_note():
    respx.get("http://scm:9876/jmx").mock(return_value=Response(200, json={"beans": []}))
    respx.get("http://scm:9876/conf").mock(return_value=Response(200, json={"properties": []}))

    context = ContainerHealthPlugin().collect_context(MISSING_ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("ReplicationManagerMetrics MBean" in note for note in context.notes)


@respx.mock
def test_collect_context_jmx_unreachable_adds_note_not_raises():
    respx.get("http://scm:9876/jmx").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("http://scm:9876/conf").mock(return_value=Response(200, json={"properties": []}))

    context = ContainerHealthPlugin().collect_context(UNHEALTHY_ALERT, CLUSTER)

    assert context.jmx_metrics == {}
    assert any("Could not reach SCM JMX endpoint" in note for note in context.notes)


def test_evaluate_alert_no_metrics_is_unconfirmed():
    confirmed, reason = ContainerHealthPlugin().evaluate_alert(
        DiagnosticContext(alert=UNDER_REPLICATED_ALERT)
    )
    assert confirmed is False
    assert "could not collect" in reason.lower()


def test_evaluate_alert_missing_confirms_real_backlog():
    context = DiagnosticContext(alert=MISSING_ALERT, jmx_metrics={"MissingContainers": 3})
    confirmed, reason = ContainerHealthPlugin().evaluate_alert(context)
    assert confirmed is True
    assert "MissingContainers=3" in reason


def test_evaluate_alert_missing_flags_likely_false_positive():
    context = DiagnosticContext(alert=MISSING_ALERT, jmx_metrics={"MissingContainers": 0})
    confirmed, reason = ContainerHealthPlugin().evaluate_alert(context)
    assert confirmed is False
    assert "stale" in reason.lower() or "resolved" in reason.lower()


def test_evaluate_alert_under_replicated_confirms_real_backlog():
    context = DiagnosticContext(
        alert=UNDER_REPLICATED_ALERT, jmx_metrics={"UnderReplicatedContainers": 12}
    )
    confirmed, reason = ContainerHealthPlugin().evaluate_alert(context)
    assert confirmed is True


def test_evaluate_alert_unhealthy_confirms_real_backlog():
    context = DiagnosticContext(alert=UNHEALTHY_ALERT, jmx_metrics={"UnhealthyContainers": 2})
    confirmed, reason = ContainerHealthPlugin().evaluate_alert(context)
    assert confirmed is True


def test_permitted_actions_for_only_allows_under_replicated():
    plugin = ContainerHealthPlugin()

    missing_actions = plugin.permitted_actions_for(DiagnosticContext(alert=MISSING_ALERT))
    unhealthy_actions = plugin.permitted_actions_for(DiagnosticContext(alert=UNHEALTHY_ALERT))
    under_replicated_actions = plugin.permitted_actions_for(
        DiagnosticContext(alert=UNDER_REPLICATED_ALERT)
    )

    assert missing_actions == []
    assert unhealthy_actions == []
    assert [action.action_id for action in under_replicated_actions] == [
        INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY
    ]


def test_build_remediation_plan_halves_under_replicated_interval():
    context = DiagnosticContext(
        alert=UNDER_REPLICATED_ALERT,
        config_properties={"hdds.scm.replication.under.replicated.interval": "30s"},
    )
    plan = ContainerHealthPlugin().build_remediation_plan(
        INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY, context
    )

    assert plan.config_changes == {"hdds.scm.replication.under.replicated.interval": "15s"}
    assert plan.requires_restart is False


def test_build_remediation_plan_rejects_missing_health_state():
    context = DiagnosticContext(alert=MISSING_ALERT)
    with pytest.raises(ValueError, match="missing/unhealthy containers"):
        ContainerHealthPlugin().build_remediation_plan(
            INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY, context
        )


def test_build_remediation_plan_rejects_unhealthy_health_state():
    context = DiagnosticContext(alert=UNHEALTHY_ALERT)
    with pytest.raises(ValueError, match="missing/unhealthy containers"):
        ContainerHealthPlugin().build_remediation_plan(
            INCREASE_UNDER_REPLICATED_QUEUE_FREQUENCY, context
        )
