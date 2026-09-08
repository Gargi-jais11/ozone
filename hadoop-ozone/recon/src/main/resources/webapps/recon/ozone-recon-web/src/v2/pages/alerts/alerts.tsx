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

import React, { useEffect, useState } from 'react';
import moment from 'moment';
import axios from 'axios';
import { Alert, Button, Descriptions, Table, Tag } from 'antd';
import { TablePaginationConfig } from 'antd/es/table';

import AutoReloadPanel from '@/components/autoReloadPanel/autoReloadPanel';
import { showDataFetchError } from '@/utils/common';
import { useApiData } from '@/v2/hooks/useAPIData.hook';
import { useAutoReload } from '@/v2/hooks/useAutoReload.hook';
import { RAG_SERVICE_BASE_URL } from '@/constants/ragService.constants';

import './alerts.less';

interface IPrometheusAlert {
  labels: Record<string, string>;
  annotations: Record<string, string>;
  state: string;
  activeAt: string;
}

interface IPrometheusAlertsResponse {
  status: string;
  data: {
    alerts: IPrometheusAlert[];
  };
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
  retrieved_documents: { source: string; snippet: string; score: number }[];
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

interface IAlertRecord extends IPrometheusAlert {
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

const DEFAULT_ALERTS_RESPONSE: IPrometheusAlertsResponse = {
  status: '',
  data: { alerts: [] }
};

const COLUMNS = [
  {
    title: 'Alert',
    dataIndex: ['labels', 'alertname'],
    key: 'alertname',
    render: (_: string, record: IAlertRecord) => record.labels.alertname ?? 'N/A'
  },
  {
    title: 'Severity',
    dataIndex: ['labels', 'severity'],
    key: 'severity',
    render: (_: string, record: IAlertRecord) => record.labels.severity ?? 'N/A'
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

const Alerts: React.FC<{}> = () => {
  const [dataSource, setDataSource] = useState<IAlertRecord[]>([]);
  const [lastUpdated, setLastUpdated] = useState<number>(0);
  const [rowState, setRowState] = useState<Record<string, IAlertRowState>>({});

  const alertsData = useApiData<IPrometheusAlertsResponse>(
    '/api/v1/metrics/alerts',
    DEFAULT_ALERTS_RESPONSE,
    {
      initialFetch: false,
      onError: (error) => showDataFetchError(error)
    }
  );

  useEffect(() => {
    if (alertsData.data) {
      const alerts: IPrometheusAlert[] = alertsData.data.data?.alerts ?? [];
      const nextDataSource: IAlertRecord[] = alerts.map((alert, idx) => ({
        ...alert,
        rowKey: `${alert.labels.alertname ?? 'alert'}-${alert.activeAt ?? idx}`
      }));
      setDataSource(nextDataSource);
      setLastUpdated(Number(moment()));
    }
  }, [alertsData.data]);

  const loadAlerts = () => {
    alertsData.refetch().catch(() => {});
  };

  const autoReload = useAutoReload(loadAlerts);

  const updateRowState = (rowKey: string, update: Partial<IAlertRowState>) => {
    setRowState(prevState => ({
      ...prevState,
      [rowKey]: { ...prevState[rowKey], ...update }
    }));
  };

  const diagnose = (record: IAlertRecord) => {
    updateRowState(record.rowKey, { diagnosing: true, diagnosisError: undefined });
    const payload = {
      labels: record.labels,
      annotations: record.annotations,
      state: record.state,
      activeAt: record.activeAt
    };
    axios.post(`${RAG_SERVICE_BASE_URL}/api/v1/diagnose`, payload).then(response => {
      updateRowState(record.rowKey, { diagnosing: false, diagnosis: response.data });
    }).catch(error => {
      updateRowState(record.rowKey, {
        diagnosing: false,
        diagnosisError: error?.message ?? 'Failed to reach the RAG diagnosis service'
      });
    });
  };

  const fix = (record: IAlertRecord) => {
    const diagnosis = rowState[record.rowKey]?.diagnosis;
    if (!diagnosis?.recommended_fix) {
      return;
    }
    updateRowState(record.rowKey, { remediating: true, remediationError: undefined });
    const payload = {
      alert: {
        labels: record.labels,
        annotations: record.annotations,
        state: record.state,
        activeAt: record.activeAt
      },
      action_id: diagnosis.recommended_fix.action_id
    };
    axios.post(`${RAG_SERVICE_BASE_URL}/api/v1/remediate`, payload, { params: { dryRun: true } }).then(response => {
      updateRowState(record.rowKey, { remediating: false, remediation: response.data });
    }).catch(error => {
      updateRowState(record.rowKey, {
        remediating: false,
        remediationError: error?.message ?? 'Failed to reach the RAG diagnosis service'
      });
    });
  };

  const expandedRowRender = (record: IAlertRecord) => {
    const state = rowState[record.rowKey] ?? { diagnosing: false, remediating: false };
    const { diagnosing, diagnosis, diagnosisError, remediating, remediation, remediationError } = state;

    return (
      <div className='alert-detail'>
        <Button type='primary' loading={diagnosing} onClick={() => diagnose(record)}>
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
                    onClick={() => fix(record)}>
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

  const paginationConfig: TablePaginationConfig = {
    showTotal: (total: number, range) => `${range[0]}-${range[1]} of ${total} alerts`,
    showSizeChanger: true
  };

  return (
    <>
      <div className='page-header-v2'>
        Alerts ({dataSource.length})
        <AutoReloadPanel
          isLoading={alertsData.loading}
          lastRefreshed={lastUpdated}
          togglePolling={autoReload.handleAutoReloadToggle}
          onReload={loadAlerts} />
      </div>
      <div className='data-container'>
        <div className='content-div'>
          <Alert type='info' showIcon className='alerts-prototype-notice'
            message='Diagnosis and Fix are a personal prototype. Fix always runs as a dry run and never mutates the cluster.' />
          <Table
            dataSource={dataSource}
            columns={COLUMNS}
            loading={alertsData.loading}
            pagination={paginationConfig}
            rowKey='rowKey'
            expandable={{ expandedRowRender }}
            scroll={{ x: 'max-content' }}
            locale={{ filterTitle: '' }} />
        </div>
      </div>
    </>
  );
};

export default Alerts;
