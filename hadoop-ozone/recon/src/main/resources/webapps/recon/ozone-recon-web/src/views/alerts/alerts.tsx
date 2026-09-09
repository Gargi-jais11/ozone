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

import React from 'react';
import moment from 'moment';
import { Alert, Button, Descriptions, Table, Tag } from 'antd';
import { TablePaginationConfig } from 'antd/es/table';

import { IAxiosResponse } from '@/types/axios.types';
import AutoReloadPanel from '@/components/autoReloadPanel/autoReloadPanel';
import { showDataFetchError } from '@/utils/common';
import { AutoReloadHelper } from '@/utils/autoReloadHelper';
import { AxiosGetHelper, AxiosPostHelper, cancelRequests } from '@/utils/axiosRequestHelper';

import './alerts.less';

interface IStoredAlert {
  id: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  state: string;
  activeAt: string;
}

interface IAiopsAlertsResponse {
  alerts: IStoredAlert[];
}

interface IRetrievedDocument {
  source: string;
  snippet: string;
  score: number;
}

interface IRecommendedFix {
  action_id: string;
  summary: string;
  config_changes: Record<string, string>;
  rationale: string;
}

interface IDiagnosisResponse {
  alert_type: string;
  diagnosis: string;
  evidence: string[];
  recommended_fix?: IRecommendedFix;
  retrieved_documents: IRetrievedDocument[];
}

interface IRemediationPlan {
  action_id: string;
  description: string;
  config_changes: Record<string, string>;
  requires_restart: boolean;
  risk: string;
  dry_run: boolean;
  applied: boolean;
  warnings: string[];
}

interface IAlertRecord extends IStoredAlert {
  rowKey: string;
}

interface IAlertRowState {
  diagnosing: boolean;
  diagnosis?: IDiagnosisResponse;
  diagnosisError?: string;
  remediating: boolean;
  remediation?: IRemediationPlan;
  remediationError?: string;
}

interface IAlertsState {
  loading: boolean;
  dataSource: IAlertRecord[];
  lastUpdated: number;
  rowState: Record<string, IAlertRowState>;
}

const COLUMNS = [
  {
    title: 'Alert',
    dataIndex: ['labels', 'alertname'],
    key: 'alertname',
    render: (_: string, record: IAlertRecord) => record.labels.alertname ?? 'N/A'
  },
  {
    title: 'Component',
    dataIndex: ['labels', 'component'],
    key: 'component',
    render: (_: string, record: IAlertRecord) => record.labels.component ?? 'N/A'
  },
  {
    title: 'Severity',
    dataIndex: ['labels', 'severity'],
    key: 'severity',
    render: (_: string, record: IAlertRecord) => {
      const severity = record.labels.severity ?? 'N/A';
      const colorBySeverity: Record<string, string> = {
        low: 'blue',
        medium: 'gold',
        high: 'orange',
        critical: 'red'
      };
      return <Tag color={colorBySeverity[severity] ?? 'default'}>{severity}</Tag>;
    }
  },
  {
    title: 'State',
    dataIndex: 'state',
    key: 'state',
    render: (state: string) => <Tag color={state === 'firing' ? 'red' : 'gold'}>{state}</Tag>
  },
  {
    title: 'Active Since',
    dataIndex: 'activeAt',
    key: 'activeAt',
    render: (activeAt: string) => activeAt ? moment(activeAt).format('lll') : 'N/A'
  },
  {
    title: 'Summary',
    dataIndex: ['annotations', 'summary'],
    key: 'summary',
    render: (_: string, record: IAlertRecord) => record.annotations.summary ?? 'N/A'
  }
];

let cancelAlertsSignal: AbortController;
let cancelDiagnoseSignal: AbortController;
let cancelRemediateSignal: AbortController;

export class Alerts extends React.Component<Record<string, object>, IAlertsState> {
  autoReload: AutoReloadHelper;

  constructor(props = {}) {
    super(props);
    this.state = {
      loading: false,
      dataSource: [],
      lastUpdated: 0,
      rowState: {}
    };
    this.autoReload = new AutoReloadHelper(this._loadData);
  }

  componentDidMount(): void {
    this._loadData();
    this.autoReload.startPolling();
  }

  componentWillUnmount(): void {
    this.autoReload.stopPolling();
    cancelRequests([
      cancelAlertsSignal,
      cancelDiagnoseSignal,
      cancelRemediateSignal
    ]);
  }

  _loadData = () => {
    this.setState({
      loading: true
    });
    const { request, controller } = AxiosGetHelper('/api/v1/aiops/alerts', cancelAlertsSignal);
    cancelAlertsSignal = controller;

    request.then((response: IAxiosResponse<IAiopsAlertsResponse>) => {
      const alerts: IStoredAlert[] = response.data?.alerts ?? [];
      const dataSource: IAlertRecord[] = alerts.map((alert) => ({
        ...alert,
        rowKey: alert.id
      }));
      this.setState({
        loading: false,
        dataSource,
        lastUpdated: Number(moment())
      });
    }).catch(error => {
      this.setState({
        loading: false
      });
      showDataFetchError(error);
    });
  };

  updateRowState = (rowKey: string, update: Partial<IAlertRowState>) => {
    this.setState(prevState => ({
      rowState: {
        ...prevState.rowState,
        [rowKey]: { ...prevState.rowState[rowKey], ...update }
      }
    }));
  };

  diagnose = (record: IAlertRecord) => {
    this.updateRowState(record.rowKey, { diagnosing: true, diagnosisError: undefined });
    const { request, controller } = AxiosPostHelper(
      `/api/v1/aiops/alerts/${encodeURIComponent(record.id)}/diagnose`,
      {},
      cancelDiagnoseSignal
    );
    cancelDiagnoseSignal = controller;
    request.then(response => {
      this.updateRowState(record.rowKey, { diagnosing: false, diagnosis: response.data });
    }).catch(error => {
      this.updateRowState(record.rowKey, {
        diagnosing: false,
        diagnosisError: error?.response?.data?.message ?? error?.message ?? 'Diagnosis failed'
      });
    });
  };

  fix = (record: IAlertRecord) => {
    const diagnosis = this.state.rowState[record.rowKey]?.diagnosis;
    if (!diagnosis?.recommended_fix) {
      return;
    }
    this.updateRowState(record.rowKey, { remediating: true, remediationError: undefined });
    const actionId = encodeURIComponent(diagnosis.recommended_fix.action_id);
    const { request, controller } = AxiosPostHelper(
      `/api/v1/aiops/alerts/${encodeURIComponent(record.id)}/remediate?dryRun=true&actionId=${actionId}`,
      {},
      cancelRemediateSignal
    );
    cancelRemediateSignal = controller;
    request.then(response => {
      this.updateRowState(record.rowKey, { remediating: false, remediation: response.data });
    }).catch(error => {
      this.updateRowState(record.rowKey, {
        remediating: false,
        remediationError: error?.response?.data?.message ?? error?.message ?? 'Remediation failed'
      });
    });
  };

  expandedRowRender = (record: IAlertRecord) => {
    const rowState = this.state.rowState[record.rowKey] ?? { diagnosing: false, remediating: false };
    const { diagnosing, diagnosis, diagnosisError, remediating, remediation, remediationError } = rowState;

    return (
      <div className='alert-detail'>
        <Button type='primary' loading={diagnosing} onClick={() => this.diagnose(record)}>
          Diagnose
        </Button>
        {diagnosisError &&
          <Alert type='error' showIcon message={diagnosisError} className='alert-detail-message' />}
        {diagnosis &&
          <Descriptions size='small' bordered column={1} className='alert-detail-descriptions'>
            <Descriptions.Item label='Diagnosis'>{diagnosis.diagnosis}</Descriptions.Item>
            {diagnosis.evidence.length > 0 &&
              <Descriptions.Item label='Evidence'>
                <ul>{diagnosis.evidence.map((item, idx) => <li key={idx}>{item}</li>)}</ul>
              </Descriptions.Item>}
            {diagnosis.recommended_fix &&
              <Descriptions.Item label='Recommended Fix'>
                {diagnosis.recommended_fix.summary}
                <div>
                  <Button type='default' loading={remediating} className='alert-detail-fix-button'
                    onClick={() => this.fix(record)}>
                    Fix (dry run)
                  </Button>
                </div>
              </Descriptions.Item>}
          </Descriptions>}
        {remediationError &&
          <Alert type='error' showIcon message={remediationError} className='alert-detail-message' />}
        {remediation &&
          <Descriptions size='small' bordered column={1} className='alert-detail-descriptions'>
            <Descriptions.Item label='Remediation Plan (dry run only, not applied)'>
              {remediation.description}
            </Descriptions.Item>
            <Descriptions.Item label='Config Changes'>
              <ul>{Object.entries(remediation.config_changes).map(([key, value]) =>
                <li key={key}>{key} = {value}</li>)}</ul>
            </Descriptions.Item>
            {remediation.warnings.length > 0 &&
              <Descriptions.Item label='Warnings'>
                <ul>{remediation.warnings.map((warning, idx) => <li key={idx}>{warning}</li>)}</ul>
              </Descriptions.Item>}
          </Descriptions>}
      </div>
    );
  };

  render() {
    const { dataSource, loading, lastUpdated } = this.state;
    const paginationConfig: TablePaginationConfig = {
      showTotal: (total: number, range) => `${range[0]}-${range[1]} of ${total} alerts`,
      showSizeChanger: true
    };
    return (
      <div className='alerts-container'>
        <div className='page-header'>
          Alerts ({dataSource.length})
          <AutoReloadPanel isLoading={loading} lastRefreshed={lastUpdated} togglePolling={this.autoReload.handleAutoReloadToggle} onReload={this._loadData} />
        </div>
        <div className='content-div'>
          <Alert type='info' showIcon className='alerts-prototype-notice'
            message='Diagnosis and Fix are a personal prototype. Fix always runs as a dry run and never mutates the cluster.' />
          <Table
            dataSource={dataSource}
            columns={COLUMNS}
            loading={loading}
            pagination={paginationConfig}
            rowKey='rowKey'
            expandable={{ expandedRowRender: this.expandedRowRender }}
            scroll={{ x: 'max-content' }}
            locale={{ filterTitle: '' }}
          />
        </div>
      </div>
    );
  }
}
