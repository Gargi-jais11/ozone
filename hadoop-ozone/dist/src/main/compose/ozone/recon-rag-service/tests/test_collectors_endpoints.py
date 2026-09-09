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

from app.collectors.endpoints import resolve_http_address
from app.config import ClusterEndpoints
from app.models import AlertPayload

CLUSTER = ClusterEndpoints(
    om_http_address="om:9874", scm_http_address="scm:9876", recon_http_address="recon:9888"
)

MALFORMED_INSTANCES = [
    "http://host:1234",
    "user@host:1234",
    "host:1234/../x",
    "not-a-host-port",
]


def _alert(alert_name: str, component: str, instance: str) -> AlertPayload:
    return AlertPayload(
        labels={"alertname": alert_name, "component": component, "instance": instance}
    )


@pytest.mark.parametrize("instance", MALFORMED_INSTANCES)
def test_resolve_http_address_rejects_malformed_instance_for_om(instance):
    alert = _alert("OzoneOmDeletionNotProgressing", "om", instance)

    http_address, warning = resolve_http_address(alert, CLUSTER)

    assert http_address == CLUSTER.om_http_address
    assert warning is not None
    assert "rejected as malformed" in warning


@pytest.mark.parametrize("instance", MALFORMED_INSTANCES)
def test_resolve_http_address_rejects_malformed_instance_for_scm(instance):
    alert = _alert("OzoneScmDeletionNotProgressing", "scm", instance)

    http_address, warning = resolve_http_address(alert, CLUSTER)

    assert http_address == CLUSTER.scm_http_address
    assert warning is not None
    assert "rejected as malformed" in warning


@pytest.mark.parametrize("instance", MALFORMED_INSTANCES)
def test_resolve_http_address_rejects_malformed_instance_for_datanode(instance):
    alert = _alert("OzoneDatanodeDeletionNotProgressing", "datanode", instance)

    http_address, warning = resolve_http_address(alert, CLUSTER)

    assert http_address == ""
    assert warning is not None
    assert "rejected as malformed" in warning


def test_resolve_http_address_missing_instance_falls_back_for_om():
    alert = AlertPayload(labels={"alertname": "OzoneOmDeletionNotProgressing", "component": "om"})

    http_address, warning = resolve_http_address(alert, CLUSTER)

    assert http_address == CLUSTER.om_http_address
    assert warning is not None
    assert "did not include instance" in warning


def test_resolve_http_address_accepts_valid_host_port():
    alert = _alert("OzoneScmDeletionNotProgressing", "scm", "scm:9876")

    http_address, warning = resolve_http_address(alert, CLUSTER)

    assert http_address == "scm:9876"
    assert warning is None
