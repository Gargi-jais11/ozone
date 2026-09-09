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

# Runbook: Ozone deletion not progressing (OM, SCM, or datanode)

## Symptom

The `OzoneDeletionNotProgressing` alert fires when any hop in the deletion
pipeline shows a backlog with zero progress rate for five minutes. The
`component` label on the alert identifies which hop is stuck:

| `component` | Service | Key metrics |
|---|---|---|
| `om` | KeyDeletingService | `numKeysProcessed` > `numKeysPurged`, purge rate zero |
| `scm` | SCMBlockDeletingService | `NumBlockDeletionTransactions` > 0, completed rate zero |
| `datanode` | BlockDeletingService | `TotalPendingBlockCount` > 0, success rate zero |

## OM hop (`component=om`)

### Likely causes, in order of frequency

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

### Recommended first action (OM)

For cause (1), raise `ozone.key.deleting.limit.per.task` to at least the live
Recon delete-pending key count (`/api/v1/keys/deletePending/summary`) so
KeyDeletingService can scan the full backlog per run. When the limit is far
below the backlog (e.g. limit=1 with 500 pending keys), doubling alone is not
enough. This property is live-reconfigurable on OM without restart.

Causes (2)-(4) may require operator investigation beyond automated remediation.

## SCM hop (`component=scm`)

### Likely causes

1. **Datanodes unavailable or slow to ack deletion commands.** SCM's
   DeletedBlockLog holds transactions until every replica datanode confirms
   block deletion.
2. **Per-interval send limit too low.** `hdds.scm.block.deletion.per-interval.max`
   caps how many block replicas SCM dispatches per SCMBlockDeletingService run.
3. **SCMBlockDeletingService not running.** Missing
   `Hadoop:service=StorageContainerManager,name=SCMBlockDeletingService` JMX bean.

### Recommended first action (SCM)

Double `hdds.scm.block.deletion.per-interval.max` when datanodes are healthy
but the DeletedBlockLog backlog is not draining. This property is reconfigurable
on SCM without restart.

## Datanode hop (`component=datanode`)

### Likely causes

1. **`ozone.block.deleting.service.interval` too large.** BlockDeletingService
   runs infrequently, so pending blocks accumulate locally.
2. **Container lock timeouts.** High `TotalLockTimeoutTransactionCount` means
   deletion commands could not acquire container locks in time.
3. **Disk or I/O issues on the datanode.** Success rate stays zero despite pending blocks.

### Recommended first action (datanode)

Halve `ozone.block.deleting.service.interval` on the affected datanode so
BlockDeletingService runs more often. This property is reconfigurable without
restart on datanodes.
