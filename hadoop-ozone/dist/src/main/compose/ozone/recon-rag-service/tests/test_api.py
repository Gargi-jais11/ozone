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

import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.main import app
from app.plugins.deletion_not_progressing import INCREASE_KEY_DELETING_LIMIT

client = TestClient(app)

ALERT_PAYLOAD = {
    "labels": {
        "alertname": "OzoneOmDeletionNotProgressing",
        "component": "om",
        "instance": "om:9874",
    },
    "annotations": {},
    "state": "firing",
    "activeAt": "2026-09-01T00:00:00Z",
}


def _mock_om_endpoints():
    respx.get("http://om:9874/jmx").mock(
        return_value=Response(
            200,
            json={
                "beans": [
                    {
                        "numKeysProcessed": 10,
                        "numKeysSentForPurge": 10,
                        "numKeysPurged": 1,
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
            json={"properties": [{"key": "ozone.key.deleting.limit.per.task", "value": "50000"}]},
        )
    )


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_plugins_lists_deletion_not_progressing():
    response = client.get("/api/v1/plugins")
    assert response.status_code == 200
    plugins = response.json()["plugins"]
    assert "OzoneOmDeletionNotProgressing" in plugins
    assert "OzoneScmDeletionNotProgressing" in plugins
    assert "OzoneDatanodeDeletionNotProgressing" in plugins


@respx.mock
def test_diagnose_unknown_alert_returns_404():
    response = client.post("/api/v1/diagnose", json={"labels": {"alertname": "SomethingUnknown"}})
    assert response.status_code == 404


@respx.mock
def test_diagnose_returns_a_diagnosis():
    _mock_om_endpoints()
    response = client.post("/api/v1/diagnose", json=ALERT_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["alert_type"] == "OzoneOmDeletionNotProgressing"
    assert body["diagnosis"]


@respx.mock
def test_remediate_dry_run_returns_plan():
    _mock_om_endpoints()
    response = client.post(
        "/api/v1/remediate",
        params={"dryRun": "true"},
        json={"alert": ALERT_PAYLOAD, "action_id": INCREASE_KEY_DELETING_LIMIT},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["applied"] is False
    assert body["config_changes"]["ozone.key.deleting.limit.per.task"] == "100000"


@respx.mock
def test_remediate_unpermitted_action_returns_400():
    _mock_om_endpoints()
    response = client.post(
        "/api/v1/remediate",
        params={"dryRun": "true"},
        json={"alert": ALERT_PAYLOAD, "action_id": "delete_everything"},
    )
    assert response.status_code == 400


@respx.mock
def test_remediate_live_execution_returns_501():
    _mock_om_endpoints()
    response = client.post(
        "/api/v1/remediate",
        params={"dryRun": "false"},
        json={"alert": ALERT_PAYLOAD, "action_id": INCREASE_KEY_DELETING_LIMIT},
    )
    assert response.status_code == 501
