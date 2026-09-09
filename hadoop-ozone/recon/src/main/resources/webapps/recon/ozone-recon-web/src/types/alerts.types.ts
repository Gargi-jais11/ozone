/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

export interface IStoredAlert {
  id: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  state: string;
  activeAt: string;
}

export interface IAiopsAlertsResponse {
  alerts: IStoredAlert[];
}

export interface IRetrievedDocument {
  source: string;
  snippet: string;
  score: number;
}

export interface IRecommendedFix {
  action_id: string;
  summary: string;
  config_changes: Record<string, string>;
  rationale: string;
}

export interface IDiagnosisResponse {
  alert_type: string;
  alert_confirmed: boolean;
  verdict_reason: string;
  what_happened: string;
  why_it_happened: string;
  how_to_fix: string;
  diagnosis?: string;
  evidence: string[];
  recommended_fix?: IRecommendedFix;
  retrieved_documents: IRetrievedDocument[];
}

export interface IRemediationPlan {
  action_id: string;
  description: string;
  config_changes: Record<string, string>;
  requires_restart: boolean;
  risk: string;
  dry_run: boolean;
  applied: boolean;
  warnings: string[];
  // Populated only when dry_run=false and applied=true: one line per step
  // recon-rag-service ran against the cluster (config edit, then
  // `ozone admin reconfig ... start`).
  execution_log: string[];
}

export interface IAlertRecord extends IStoredAlert {
  rowKey: string;
}

export interface IAlertDiagnoseLocationState {
  alert?: IStoredAlert;
}

/**
 * Default remediation action per Prometheus alertname, matching each
 * plugin's permitted_actions_for() in recon-rag-service. Alerts with no
 * entry here (e.g. OzoneScmContainerMissing/Unhealthy) never get an
 * automated fix from the backend either -- those health states require
 * operator investigation, not a config change.
 */
export const ACTION_ID_BY_ALERTNAME: Record<string, string> = {
  OzoneOmDeletionNotProgressing: 'increase_key_deleting_limit_per_task',
  OzoneScmDeletionNotProgressing: 'increase_scm_block_deletion_per_interval_max',
  OzoneDatanodeDeletionNotProgressing: 'decrease_datanode_block_deleting_interval',
  OzoneScmContainerUnderReplicated: 'increase_under_replicated_queue_processing_frequency'
};

/** Placeholder config changes for UI preview when the backend omits recommended_fix. */
export const PREVIEW_CONFIG_CHANGES_BY_ALERTNAME: Record<string, Record<string, string>> = {
  OzoneOmDeletionNotProgressing: { 'ozone.key.deleting.limit.per.task': '100000' },
  OzoneScmDeletionNotProgressing: { 'hdds.scm.block.deletion.per-interval.max': '1000000' },
  OzoneDatanodeDeletionNotProgressing: { 'ozone.block.deleting.service.interval': '30s' },
  OzoneScmContainerUnderReplicated: { 'hdds.scm.replication.under.replicated.interval': '15s' }
};

// Result of a real POST .../remediate?dryRun=false&actionId=... call --
// recon-rag-service edited the target container's config and ran
// `ozone admin reconfig ... start` (see app/remediation/live_apply.py).
export interface IApplyFixResult {
  appliedAt: string;
  plan: IRemediationPlan;
}
