# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

*** Settings ***
Documentation       Test ozone admin datanode diskbalancer command
Library             OperatingSystem
Resource            ../commonlib.robot

*** Keywords ***
Check Balancer Status
    [Arguments]         ${expected_status}
    ${result} =         Execute             ozone admin datanode diskbalancer status
    Should Contain      ${result}           ${expected_status}

** Test Cases ***
Check failure with non-admin user to start, stop and update diskbalancer
    Requires admin privilege     ozone admin datanode diskbalancer start -a
    Requires admin privilege     ozone admin datanode diskbalancer stop -a
    Requires admin privilege     ozone admin datanode diskbalancer update -t 0.0002 -a

Check success with admin user for start, stop and update diskbalancer
    Run Keyword         Kinit test user                 testuser                testuser.keytab
    ${result} =         Execute                         ozone admin datanode diskbalancer start -a
                        Should Contain                  ${result}                Start DiskBalancer on datanode(s)
    ${result} =         Execute                         ozone admin datanode diskbalancer stop -a
                        Should Contain                  ${result}                Stopping DiskBalancer on datanode(s)
    ${result} =         Execute                         ozone admin datanode diskbalancer update -t 0.0002 -a
                        Should Contain                  ${result}                Update DiskBalancer Configuration on datanode(s)

Check success with non-admin user for status and report diskbalancer
    Run Keyword         Kinit test user                 testuser2               testuser2.keytab
    ${result} =         Execute                         ozone admin datanode diskbalancer status
                        Should Contain                  ${result}                Status result:
    ${result} =         Execute                         ozone admin datanode diskbalancer report
                        Should Contain                  ${result}                Report result:

Check if balancer stops automatically
    Run Keyword         Kinit test user                 testuser                testuser.keytab
    Execute             ozone admin datanode diskbalancer start -a

    # Wait until the balancer status contains "RUNNING", retry every 5s for up to 1 min
    Wait Until Keyword Succeeds   25 sec   2 sec   Check Balancer Status   RUNNING

    # Wait until the balancer status contains "STOPPED", retry every 5s for up to 2 min
    Wait Until Keyword Succeeds   50 sec   2 sec   Check Balancer Status   STOPPED
