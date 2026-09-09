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

# Reference: deletion-related configuration properties

Defaults below are as declared in
`hadoop-hdds/common/src/main/resources/ozone-default.xml`.

| Property | Default | Effect |
|---|---|---|
| `ozone.key.deleting.limit.per.task` | 50000 | Max keys KeyDeletingService scans per run. |
| `ozone.snapshot.key.deleting.limit.per.task` | 50000 | Same, for the per-snapshot key deleting service. |
| `ozone.block.deleting.service.interval` | 1m | How often the datanode block deleting service runs. |
| `ozone.block.deleting.container.limit.per.interval` | (unset/default) | Caps how many containers are processed for block deletion per interval. |
| `ozone.snapshot.deep.cleaning.enabled` | true | Whether snapshot deep cleaning reclaims keys held live only by snapshots. |
| `hdds.scm.block.deletion.per-interval.max` | 500000 | Max block replicas SCM sends for deletion per interval. |
| `hdds.scm.block.deleting.service.interval` | 60s | How often SCMBlockDeletingService runs. |

## DeletingServiceMetrics (OM JMX)

Bean name: `Hadoop:service=OzoneManager,name=DeletingServiceMetrics`.

Key fields: `numKeysProcessed`, `numKeysSentForPurge`, `numKeysPurged`,
`numDirsSentForPurge`, `numDirsPurged`, `metricsResetTimeStamp`.

## SCMBlockDeletingService (SCM JMX)

Bean name: `Hadoop:service=StorageContainerManager,name=SCMBlockDeletingService`.

Key fields: `NumBlockDeletionTransactions` (DeletedBlockLog backlog),
`NumBlockDeletionTransactionCompleted`, `NumBlockDeletionCommandSent`,
`NumBlockDeletionCommandSuccess`, `NumBlockDeletionCommandFailure`.

## BlockDeletingService (datanode JMX)

Bean name: `Hadoop:service=HddsDatanode,name=BlockDeletingService`.

Key fields: `TotalPendingBlockCount`, `TotalPendingBlockBytes`, `SuccessCount`,
`FailureCount`, `ProcessedTransactionFailCount`, `TotalLockTimeoutTransactionCount`.
