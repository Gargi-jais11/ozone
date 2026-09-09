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
}

export interface IAlertRecord extends IStoredAlert {
  rowKey: string;
}

export interface IAlertDiagnoseLocationState {
  alert?: IStoredAlert;
}

/** Default remediation action per deletion alert component (matches recon-rag-service plugins). */
export const DELETION_ACTION_BY_COMPONENT: Record<string, string> = {
  om: 'increase_key_deleting_limit_per_task',
  scm: 'increase_scm_block_deletion_per_interval_max',
  datanode: 'decrease_datanode_block_deleting_interval'
};

/** Placeholder config changes for UI preview when the backend omits recommended_fix. */
export const DELETION_PREVIEW_CONFIG_BY_COMPONENT: Record<string, Record<string, string>> = {
  om: { 'ozone.key.deleting.limit.per.task': '100000' },
  scm: { 'hdds.scm.block.deletion.per-interval.max': '1000000' },
  datanode: { 'ozone.block.deleting.service.interval': '30s' }
};

// UI-only preview of what "apply fix" will look like once live remediation
// is implemented on the backend. No cluster mutation happens today; values
// here are simulated client-side so the interaction can be reviewed and
// signed off before the real execution path (Recon -> Ozone reconfigure) is built.
export interface IApplyFixResult {
  status: 'success' | 'failed';
  appliedAt: string;
  configChanges: Record<string, string>;
}
