#!/usr/bin/env bash
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

# Drives a real key-deletion backlog on an already-running dev cluster
# (started via ./run.sh, OM's 9874 port published to the host) so the
# OzoneDeletionNotProgressing Prometheus alert (see ozone-aiops-alerts.yml)
# has a chance to fire, for demo purposes.
#
# The alert fires on rate(deleting_service_metrics_num_keys_purged[10m]) == 0
# and numKeysProcessed > numKeysPurged (for: 5m). numKeysProcessed increments
# as soon as KeyDeletingService picks up a deleted key for processing, but
# numKeysPurged only increments once OM's Ratis state machine commits the
# matching PurgeKeys request. That commit re-validates the bucket's snapshot
# chain against the chain state captured when the batch started
# (OMKeyPurgeRequest#validateAndUpdateCache); if a new snapshot lands on the
# bucket in between, the purge is rejected and the earlier "processed" count
# is never repaid.
#
# The race window this needs to hit is tiny (well under a second: the time
# between KeyDeletingService capturing the bucket's expected snapshot ID and
# the PurgeKeysRequest actually committing through the OM double buffer), but
# KeyDeletingService itself only wakes up once per
# ozone.block.deleting.service.interval (default 60s). A single create+sleep
# 2s+delete per batch is very unlikely to have an active snapshot at the
# instant some future tick happens to run, which is why processed/purged were
# observed jumping together in lockstep: by the time the service ticks, the
# earlier snapshot is long gone. Increasing the deletion interval does not
# help either -- it only makes ticks rarer, and each tick still updates both
# metrics synchronously within the same call, so the same lockstep symptom
# just repeats less often.
#
# Instead of timing one snapshot per batch, this script runs a background
# loop that continuously creates+deletes a snapshot on the bucket for the
# whole run, turning every KeyDeletingService tick (whenever it happens to
# land) into a race attempt instead of just one per batch.
#
# Usage: ./trigger-deletion-not-progressing-alert.sh
# Tunables (env vars): VOLUME, BUCKET, BATCH_KEYS, ITERATIONS, POLL_INTERVAL,
# STABLE_POLLS, SNAPSHOT_TOGGLE_INTERVAL.

set -u -o pipefail

COMPOSE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$COMPOSE_DIR" || exit 1

# shellcheck source=/dev/null
source "$COMPOSE_DIR/../compose_v2_compatibility.sh"

VOLUME="${VOLUME:-alertdemo}"
BUCKET="${BUCKET:-alertdemo}"
BATCH_KEYS="${BATCH_KEYS:-300}"
ITERATIONS="${ITERATIONS:-20}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
STABLE_POLLS="${STABLE_POLLS:-3}"
SNAPSHOT_TOGGLE_INTERVAL="${SNAPSHOT_TOGGLE_INTERVAL:-1}"

om_metric() {
  curl -s http://localhost:9874/prom | grep "^deleting_service_metrics_num_keys_$1{" | awk '{print $NF}'
}

echo "Ensuring /$VOLUME/$BUCKET exists (OBJECT_STORE layout, so deleted keys are"
echo "tracked by numKeysProcessed/numKeysPurged rather than the directory-purge"
echo "metrics used by FSO buckets)..."
docker-compose exec -T om ozone sh volume create "/$VOLUME" >/dev/null 2>&1
docker-compose exec -T om ozone sh bucket create -l OBJECT_STORE "/$VOLUME/$BUCKET" >/dev/null 2>&1

# Continuously create+delete a snapshot on the bucket for the whole run, so
# whichever moment KeyDeletingService actually ticks and commits a
# PurgeKeysRequest, there is a good chance a snapshot is mid-transition
# (present at capture time, gone by commit time, or vice versa).
snapshot_toggle_loop() {
  local n=0
  while true; do
    n=$((n + 1))
    docker-compose exec -T om ozone sh snapshot create "/$VOLUME/$BUCKET" "race-$$-$n" >/dev/null 2>&1
    sleep "$SNAPSHOT_TOGGLE_INTERVAL"
    docker-compose exec -T om ozone sh snapshot delete "/$VOLUME/$BUCKET" "race-$$-$n" >/dev/null 2>&1
    sleep "$SNAPSHOT_TOGGLE_INTERVAL"
  done
}
snapshot_toggle_loop &
toggle_pid=$!
trap 'kill "$toggle_pid" 2>/dev/null' EXIT

prev_gap=-1
stable_count=0

for i in $(seq 1 "$ITERATIONS"); do
  prefix="alertdemo-$$-$i"
  echo "[$i/$ITERATIONS] writing and deleting $BATCH_KEYS keys (prefix=$prefix)..."
  docker-compose exec -T om ozone freon ockg -t 4 -n "$BATCH_KEYS" -v "$VOLUME" -b "$BUCKET" -p "$prefix" >/dev/null 2>&1
  docker-compose exec -T om ozone freon ockr -t 4 -n "$BATCH_KEYS" -v "$VOLUME" -b "$BUCKET" -p "$prefix" >/dev/null 2>&1

  processed=$(om_metric processed)
  purged=$(om_metric purged)
  processed="${processed:-0}"
  purged="${purged:-0}"
  gap=$((processed - purged))
  echo "    numKeysProcessed=$processed numKeysPurged=$purged gap=$gap"

  if [ "$gap" -gt 0 ] && [ "$gap" -eq "$prev_gap" ]; then
    stable_count=$((stable_count + 1))
  else
    stable_count=0
  fi
  prev_gap="$gap"

  if [ "$stable_count" -ge "$STABLE_POLLS" ]; then
    echo ""
    echo "A backlog gap of $gap key(s) has stayed open across $STABLE_POLLS consecutive polls."
    echo "OzoneDeletionNotProgressing needs numKeysPurged's rate to stay at zero for 5"
    echo "uninterrupted minutes (the rule's 'for: 5m') before it moves from pending to"
    echo "firing. Watch it at http://localhost:9090/alerts (requires the monitoring.yaml"
    echo "compose add-on) or on Recon's Alerts page."
    echo "To reset OM's in-memory metrics for a fresh repeat demo: docker-compose restart om"
    exit 0
  fi

  sleep "$POLL_INTERVAL"
done

echo ""
echo "No stable backlog gap opened in $ITERATIONS iterations."
echo "This relies on winning a timing-dependent race inside OM's purge path, so it"
echo "doesn't always land on the first try. Re-run the script, or raise ITERATIONS."
echo "To reset OM's in-memory metrics for a clean retry: docker-compose restart om"
exit 1
