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

from app.collectors.recon_client import ReconFetchError, fetch_deleted_key_summary

SUMMARY_URL = "http://recon:9888/api/v1/keys/deletePending/summary"


@respx.mock
def test_fetch_deleted_key_summary_returns_parsed_json():
    respx.get(SUMMARY_URL).mock(
        return_value=Response(
            200,
            json={
                "totalDeletedKeys": 8,
                "totalReplicatedDataSize": 90000,
                "totalUnreplicatedDataSize": 30000,
            },
        )
    )

    summary = fetch_deleted_key_summary("recon:9888")

    assert summary["totalDeletedKeys"] == 8


@respx.mock
def test_fetch_deleted_key_summary_raises_on_unreachable_endpoint():
    respx.get(SUMMARY_URL).mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(ReconFetchError):
        fetch_deleted_key_summary("recon:9888")


@respx.mock
def test_fetch_deleted_key_summary_raises_on_malformed_json():
    respx.get(SUMMARY_URL).mock(return_value=Response(200, text="not json"))

    with pytest.raises(ReconFetchError):
        fetch_deleted_key_summary("recon:9888")


@respx.mock
def test_fetch_deleted_key_summary_raises_on_error_status():
    respx.get(SUMMARY_URL).mock(return_value=Response(500, text="internal error"))

    with pytest.raises(ReconFetchError):
        fetch_deleted_key_summary("recon:9888")
