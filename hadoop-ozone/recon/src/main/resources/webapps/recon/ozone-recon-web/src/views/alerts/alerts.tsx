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
import { Alert, Button, Table, Tag } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import { TablePaginationConfig } from 'antd/es/table';
import { RouteComponentProps } from 'react-router-dom';

import { IAxiosResponse } from '@/types/axios.types';
import { IAiopsAlertsResponse, IAlertRecord } from '@/types/alerts.types';
import AutoReloadPanel from '@/components/autoReloadPanel/autoReloadPanel';
import { showDataFetchError } from '@/utils/common';
import { AutoReloadHelper } from '@/utils/autoReloadHelper';
import { AxiosGetHelper, cancelRequests } from '@/utils/axiosRequestHelper';

import './alerts.less';

interface IAlertsState {
  loading: boolean;
  dataSource: IAlertRecord[];
  lastUpdated: number;
}

const SEVERITY_COLORS: Record<string, string> = {
  low: 'blue',
  medium: 'gold',
  high: 'orange',
  critical: 'red'
};

let cancelAlertsSignal: AbortController;

export class Alerts extends React.Component<RouteComponentProps, IAlertsState> {
  autoReload: AutoReloadHelper;

  constructor(props: RouteComponentProps) {
    super(props);
    this.state = {
      loading: false,
      dataSource: [],
      lastUpdated: 0
    };
    this.autoReload = new AutoReloadHelper(this._loadData);
  }

  componentDidMount(): void {
    this._loadData();
    this.autoReload.startPolling();
  }

  componentWillUnmount(): void {
    this.autoReload.stopPolling();
    cancelRequests([cancelAlertsSignal]);
  }

  _loadData = () => {
    this.setState({
      loading: true
    });
    const { request, controller } = AxiosGetHelper('/api/v1/aiops/alerts', cancelAlertsSignal);
    cancelAlertsSignal = controller;

    request.then((response: IAxiosResponse<IAiopsAlertsResponse>) => {
      const alerts = response.data?.alerts ?? [];
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

  openDiagnose = (record: IAlertRecord) => {
    this.props.history.push({
      pathname: `/Alerts/Diagnose/${encodeURIComponent(record.id)}`,
      state: { alert: record }
    });
  };

  render() {
    const { dataSource, loading, lastUpdated } = this.state;
    const paginationConfig: TablePaginationConfig = {
      showTotal: (total: number, range) => `${range[0]}-${range[1]} of ${total} alerts`,
      showSizeChanger: true
    };

    const columns = [
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
          return <Tag color={SEVERITY_COLORS[severity] ?? 'default'}>{severity}</Tag>;
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
      },
      {
        title: 'Actions',
        key: 'actions',
        fixed: 'right' as const,
        render: (_: string, record: IAlertRecord) => (
          <Button type='primary' icon={<RobotOutlined />} onClick={() => this.openDiagnose(record)}>
            Diagnose
          </Button>
        )
      }
    ];

    return (
      <div className='alerts-container'>
        <div className='page-header'>
          Alerts ({dataSource.length})
          <AutoReloadPanel isLoading={loading} lastRefreshed={lastUpdated} togglePolling={this.autoReload.handleAutoReloadToggle} onReload={this._loadData} />
        </div>
        <div className='content-div'>
          <Alert type='info' showIcon className='alerts-prototype-notice'
            message='Click Diagnose to open an AI-powered analysis page. Fix preview always runs as a dry run and never mutates the cluster.' />
          <Table
            dataSource={dataSource}
            columns={columns}
            loading={loading}
            pagination={paginationConfig}
            rowKey='rowKey'
            scroll={{ x: 'max-content' }}
            locale={{ filterTitle: '' }}
          />
        </div>
      </div>
    );
  }
}

export default Alerts;
