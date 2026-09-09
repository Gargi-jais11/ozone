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

# Runbook: SCM container health (missing / under-replicated / unhealthy)

## Symptom

SCM's ReplicationManager continuously classifies every container into a
health state and publishes the count of each via the
`ReplicationManagerMetrics` JMX bean
(`Hadoop:service=StorageContainerManager,name=ReplicationManagerMetrics`).
Three alerts fire when the corresponding count stays above zero:

| alertname | `health_state` label | Metric | Meaning |
|---|---|---|---|
| `OzoneScmContainerMissing` | `missing` | `MissingContainers` | No online replicas at all. |
| `OzoneScmContainerUnderReplicated` | `under_replicated` | `UnderReplicatedContainers` | Fewer replicas than the replication factor requires. |
| `OzoneScmContainerUnhealthy` | `unhealthy` | `UnhealthyContainers` | Closed/quasi-closed with replicas in inconsistent states. |

`UnderReplicatedQueueSize` and `OverReplicatedQueueSize` show how much work
ReplicationManager currently has queued.

## Missing containers (`health_state=missing`)

This is the most severe of the three: the container has **zero** online
replicas, so the data it holds is currently unavailable (and, if all replicas
were lost rather than merely offline, unrecoverable).

### Likely causes

1. All datanodes holding a replica are down, decommissioned, or have a failed
   volume at the same time (check `SCMNodeMetrics` dead/stale node counts and
   `VolumeFailures`).
2. A replica set was under-replicated for long enough that the last
   remaining copies were lost before ReplicationManager could heal it.

### Recommended action

No automated remediation is proposed for this alert. Investigate datanode and
disk health for the affected containers immediately; this is an operator
escalation, not a config tuning problem.

## Under-replicated containers (`health_state=under_replicated`)

### Likely causes, in order of frequency

1. **ReplicationManager isn't processing the queue fast enough.**
   `hdds.scm.replication.under.replicated.interval` (default `30s`) controls
   how often the under-replicated queue is checked; a large backlog combined
   with the default interval can look stuck even though nodes are healthy.
2. **Insufficient healthy target datanodes** for the container's placement
   policy (e.g. not enough distinct racks/nodes with capacity).
3. **`hdds.scm.replication.datanode.replication.limit`** (default `20`) caps
   how many replication commands can be queued per datanode; a cluster-wide
   replication event (e.g. several nodes decommissioned at once) can exceed
   this and slow the drain rate.

### Recommended first action

Halve `hdds.scm.replication.under.replicated.interval` so ReplicationManager
re-checks the queue more often. This property is reconfigurable on SCM
without a restart. If the queue is large because of insufficient target
nodes rather than processing frequency, this will not help -- check
datanode capacity and placement policy constraints first.

## Unhealthy containers (`health_state=unhealthy`)

Closed or quasi-closed containers where replicas disagree on state (e.g. one
replica thinks it's `CLOSED`, another thinks `QUASI_CLOSED`, or `OPEN`).

### Likely causes

1. A datanode crashed mid-close, leaving its replica in an inconsistent state.
2. A network partition during a Ratis pipeline transition.

### Recommended action

No automated remediation is proposed for this alert. These containers need
per-container investigation (`ozone admin container info <id>`) to determine
which replica is authoritative before any repair action is safe.
