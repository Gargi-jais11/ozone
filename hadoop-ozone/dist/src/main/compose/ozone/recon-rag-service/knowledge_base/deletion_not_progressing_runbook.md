<!---
 Licensed to the Apache Software Foundation (ASF) under one or more
 contributor license agreements.  See the NOTICE file distributed with
 this work for additional information regarding copyright ownership.
 The ASF licenses this file to you under the Apache License, Version 2.0
 (the "License"); you may not use this file except in compliance with
 the License.  You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
-->

# Runbook: Ozone key deletion not progressing

## Symptom

The `OzoneDeletionNotProgressing` alert fires when the Ozone Manager's
KeyDeletingService appears to be making little or no forward progress:
`numKeysProcessed` and `numKeysPurged` (from the `DeletingServiceMetrics`
JMX bean) stay flat across scrape intervals while keys/directories remain
queued for deletion.

## Likely causes, in order of frequency

1. **Backlog exceeds the per-task scan limit.** `ozone.key.deleting.limit.per.task`
   (default 50000) bounds how many keys KeyDeletingService inspects per run.
   A large accumulated delete backlog combined with the default limit can make
   progress look stalled even though the service is healthy and running.
2. **Snapshot chain is holding keys live.** If `ozone.snapshot.deep.cleaning.enabled`
   is `true` (the default) but deep cleaning itself is behind, keys referenced by
   an older snapshot cannot be purged yet even though they were "deleted" from
   the active namespace.
3. **Downstream block deletion is the actual bottleneck.** Keys purged from the
   deleted table still need their blocks deleted via the block deletion pipeline
   (`ozone.block.deleting.service.interval`,
   `ozone.block.deleting.container.limit.per.interval`). If that pipeline is
   stalled (e.g. datanode unavailability), the deleted table can back up even
   though KeyDeletingService itself is functioning normally.
4. **The service is not running at all.** If the `DeletingServiceMetrics` MBean
   is missing entirely from OM's `/jmx` output, the background service thread
   may not have started.

## Recommended first action

For cause (1), doubling `ozone.key.deleting.limit.per.task` lets the service
scan a larger backlog per run without any other configuration change. This is
the only remediation this plugin proposes automatically; it requires an OM
restart or reconfig to take effect and should be treated as medium risk since
a much larger per-task limit increases the work done in a single run.

Causes (2)-(4) require operator investigation beyond what this plugin
automates in this build (see the deferred-work section of the service README).
