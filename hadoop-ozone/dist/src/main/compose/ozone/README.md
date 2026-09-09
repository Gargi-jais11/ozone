<!---
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License. See accompanying LICENSE file.
-->

# Compose file with optional monitoring and profiling configs

This directory contains a docker-compose definition for an Ozone cluster with all components (including S3 Gateway and Recon).

There are three optional add-ons:

 * monitoring: adds Grafana, Jaeger and Prometheus services, and configures Ozone to work with them
 * profiling: allows sampling Ozone CPU/memory using [async-profiler](https://github.com/jvm-profiling-tools/async-profiler)
 * rag-service: adds the `recon-rag-service` prototype (see
   [`recon-rag-service/README.md`](recon-rag-service/README.md)), a pluggable
   RAG pipeline for diagnosing Ozone alerts and proposing dry-run-only fixes.
   Requires the monitoring add-on as well, since it reads alerts via
   Recon's Prometheus proxy.

## How to start

TL;DR:

1. single datanode:
   ```
   ./run.sh -d
   ```
2. multiple datanodes for replication:
   ```
   OZONE_DATANODES=3 ./run.sh -d
   # or
   OZONE_DATANODES=5 ./run.sh -d
   ```

### Basics

The cluster can be started with regular `docker-compose up` command.  Use `-d` to start the cluster in the background.

You can change the number of datanodes to start using the `--scale` option.  Eg. to start 3 datanodes: `docker-compose up -d --scale datanode=3`.

The cluster's replication factor (1 or 3) can be controlled by setting the `OZONE_REPLICATION_FACTOR` environment variable.  It defaults to 1 to match the number of datanodes started by default, without the `--scale` option.

For convenience the `run.sh` script can be used to start multiple datanodes (by setting the `OZONE_DATANODES` variable), while making sure the replication factor and the number of datanodes are compatible.  It also passes any additional arguments provided on the command-line (eg. `-d`) to `docker-compose`.

### Add-ons

Monitoring and/or performance add-ons can be enabled via docker-compose's ability to use multiple compose files (by using the [`-f` option repeatedly](https://docs.docker.com/compose/reference/overview/#specifying-multiple-compose-files), or more easily by defining the [`COMPOSE_FILE` environment variable](https://docs.docker.com/compose/reference/envvars/#compose_file)):

```
# no COMPOSE_FILE var                                                  # => only Ozone
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml                # => add monitoring
export COMPOSE_FILE=docker-compose.yaml:profiling.yaml                 # => add profiling
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml:profiling.yaml # => add both
export COMPOSE_FILE=docker-compose.yaml:monitoring.yaml:rag-service.yaml # => add the RAG diagnosis service (needs monitoring)
```

Once the variable is defined, Ozone cluster with add-ons can be started/scaled/stopped etc. using the same `docker-compose` commands as for the base cluster.

### Load generator

Ozone comes with a load generator called Freon.

You can enter one of the containers (eg. SCM) and start a Freon test:

```
docker-compose exec scm bash
ozone freon ockg -n1000
```

You can also start two flavors of Freon as separate services, which allows scaling them up.  Once all the datanodes are started, start Freon by adding its definition to `COMPOSE_FILE` and re-running the `docker-compose up` or `run.sh` command:

```
export COMPOSE_FILE="${COMPOSE_FILE}:freon-ockg.yaml"

docker-compose up -d --no-recreate --scale datanode=3
# OR
./run.sh -d
```

## How to use

You can check the ozone web ui:

OzoneManager: http://localhost:9874
SCM: http://localhost:9876

### Monitoring

 * Prometheus: follows a pull based approach where metrics are published on an HTTP endpoint.  Metrics can be checked on [Prometheus' web UI](http://localhost:9090/)
 * Grafana: comes with three [dashboards](http://localhost:3000) for Ozone
   * Ozone - Object Metrics
   * Ozone - RPC Metrics
   * Ozone - Overall Metrics
 * Jaeger: collects distributed tracing information from Ozone, can be queried on the [Jaeger web UI](http://localhost:16686)

### Profiling

Start by hitting the `/prof` endpoint on the service to be profiled, eg. http://localhost:9876/prof for SCM.  [Detailed instructions](https://cwiki.apache.org/confluence/display/HADOOP/Java+Profiling+of+Ozone) can be found in the Hadoop wiki.

### RAG diagnosis service (prototype)

With the `rag-service` add-on enabled (in addition to `monitoring`), the
`recon-rag-service` container is available at http://localhost:8642 (see
[`recon-rag-service/README.md`](recon-rag-service/README.md) for its API and
architecture) and a new "Alerts" page appears in the Recon UI at
http://localhost:9888, listing active Prometheus alerts with "Diagnose" and
"Fix" buttons. This is a personal prototype: remediation is diagnosis +
dry-run only and never mutates the cluster.

### Triggering the OzoneDeletionNotProgressing alert (demo)

`trigger-deletion-not-progressing-alert.sh` drives a real key-deletion
backlog on an already-running cluster so the `OzoneDeletionNotProgressing`
Prometheus alert (`ozone-aiops-alerts.yml`) has a chance to fire, for demos:

```
./trigger-deletion-not-progressing-alert.sh
```

It repeatedly writes and deletes a batch of keys, racing a short-lived
snapshot against each batch to open a gap between OM's `numKeysProcessed`
and `numKeysPurged` metrics, and polls until that gap holds steady. This
exploits a timing-dependent race inside OM's purge path, so it does not
always succeed on the first run; re-run it (or raise `ITERATIONS`) if no gap
opens. Once a gap opens it still needs to hold for 5 uninterrupted minutes
(`for: 5m`) before the alert moves from pending to firing; watch it on
[Prometheus' web UI](http://localhost:9090/alerts) (requires the
`monitoring` add-on) or on Recon's Alerts page. To reset OM's in-memory
metrics for a clean repeat: `docker-compose restart om`.
