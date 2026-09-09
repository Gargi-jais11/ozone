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
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Modal,
  Spin,
  Tag
} from 'antd';
import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { Link, RouteComponentProps } from 'react-router-dom';

import { IAxiosResponse } from '@/types/axios.types';
import {
  DELETION_ACTION_BY_COMPONENT,
  DELETION_PREVIEW_CONFIG_BY_COMPONENT,
  IAiopsAlertsResponse,
  IAlertDiagnoseLocationState,
  IApplyFixResult,
  IDiagnosisResponse,
  IRecommendedFix,
  IRemediationPlan,
  IStoredAlert
} from '@/types/alerts.types';
import { showDataFetchError } from '@/utils/common';
import { AxiosGetHelper, AxiosPostHelper, cancelRequests } from '@/utils/axiosRequestHelper';

import './alertDiagnose.less';

interface IAlertDiagnoseParams {
  alertId: string;
}

interface IAlertDiagnoseState {
  alert?: IStoredAlert;
  loadingAlert: boolean;
  diagnosing: boolean;
  diagnosis?: IDiagnosisResponse;
  diagnosisError?: string;
  remediating: boolean;
  remediation?: IRemediationPlan;
  remediationError?: string;
  applying: boolean;
  applyResult?: IApplyFixResult;
}

const SEVERITY_COLORS: Record<string, string> = {
  low: 'blue',
  medium: 'gold',
  high: 'orange',
  critical: 'red'
};

let cancelAlertSignal: AbortController;
let cancelDiagnoseSignal: AbortController;
let cancelRemediateSignal: AbortController;

export class AlertDiagnose extends React.Component<
  RouteComponentProps<IAlertDiagnoseParams, object, IAlertDiagnoseLocationState>,
  IAlertDiagnoseState
> {
  constructor(props: RouteComponentProps<IAlertDiagnoseParams, object, IAlertDiagnoseLocationState>) {
    super(props);
    this.state = {
      loadingAlert: true,
      diagnosing: false,
      remediating: false,
      applying: false
    };
  }

  componentDidMount(): void {
    this.loadAlertAndDiagnose();
  }

  componentWillUnmount(): void {
    cancelRequests([cancelAlertSignal, cancelDiagnoseSignal, cancelRemediateSignal]);
  }

  getAlertId = (): string => this.props.match.params.alertId;

  loadAlertAndDiagnose = () => {
    const alertFromNav = this.props.location.state?.alert;
    if (alertFromNav && alertFromNav.id === this.getAlertId()) {
      this.setState({ alert: alertFromNav, loadingAlert: false }, this.runDiagnosis);
      return;
    }

    const { request, controller } = AxiosGetHelper('/api/v1/aiops/alerts', cancelAlertSignal);
    cancelAlertSignal = controller;
    request.then((response: IAxiosResponse<IAiopsAlertsResponse>) => {
      const alerts = response.data?.alerts ?? [];
      const alert = alerts.find(item => item.id === this.getAlertId());
      if (!alert) {
        this.setState({
          loadingAlert: false,
          diagnosisError: 'Alert not found. It may have been resolved or removed.'
        });
        return;
      }
      this.setState({ alert, loadingAlert: false }, this.runDiagnosis);
    }).catch(error => {
      this.setState({ loadingAlert: false });
      showDataFetchError(error);
    });
  };

  runDiagnosis = () => {
    this.setState({ diagnosing: true, diagnosisError: undefined });
    const { request, controller } = AxiosPostHelper(
      `/api/v1/aiops/alerts/${encodeURIComponent(this.getAlertId())}/diagnose`,
      {},
      cancelDiagnoseSignal
    );
    cancelDiagnoseSignal = controller;
    request.then(response => {
      this.setState({ diagnosing: false, diagnosis: response.data });
    }).catch(error => {
      this.setState({
        diagnosing: false,
        diagnosisError: error?.response?.data?.message ?? error?.message ?? 'Diagnosis failed'
      });
    });
  };

  resolveActionId = (diagnosis: IDiagnosisResponse, alert?: IStoredAlert): string | undefined => {
    if (diagnosis.recommended_fix?.action_id) {
      return diagnosis.recommended_fix.action_id;
    }
    const component = alert?.labels.component?.toLowerCase() ?? 'om';
    return DELETION_ACTION_BY_COMPONENT[component];
  };

  resolveRemediationPreview = (diagnosis: IDiagnosisResponse, alert?: IStoredAlert): IRecommendedFix => {
    if (diagnosis.recommended_fix) {
      return diagnosis.recommended_fix;
    }
    const component = alert?.labels.component?.toLowerCase() ?? 'om';
    const actionId = DELETION_ACTION_BY_COMPONENT[component] ?? DELETION_ACTION_BY_COMPONENT.om;
    const configChanges = DELETION_PREVIEW_CONFIG_BY_COMPONENT[component]
      ?? DELETION_PREVIEW_CONFIG_BY_COMPONENT.om;
    return {
      action_id: actionId,
      summary: diagnosis.how_to_fix || 'Apply the suggested configuration change for this alert.',
      config_changes: configChanges,
      rationale: diagnosis.why_it_happened || ''
    };
  };

  fix = () => {
    const { diagnosis, alert } = this.state;
    if (!diagnosis) {
      return;
    }
    const actionId = this.resolveActionId(diagnosis, alert);
    if (!actionId) {
      this.setState({ remediationError: 'No remediation action is available for this alert type.' });
      return;
    }
    this.setState({ remediating: true, remediationError: undefined });
    const encodedActionId = encodeURIComponent(actionId);
    const { request, controller } = AxiosPostHelper(
      `/api/v1/aiops/alerts/${encodeURIComponent(this.getAlertId())}/remediate?dryRun=true&actionId=${encodedActionId}`,
      {},
      cancelRemediateSignal
    );
    cancelRemediateSignal = controller;
    request.then(response => {
      this.setState({ remediating: false, remediation: response.data });
    }).catch(error => {
      this.setState({
        remediating: false,
        remediationError: error?.response?.data?.message ?? error?.message ?? 'Remediation failed'
      });
    });
  };

  // NOTE: This is a UI-only preview of what "Apply Fix" will look like once
  // live remediation is implemented on the backend. It never contacts the
  // cluster or recon-rag-service -- the result below is simulated so the
  // interaction/UX can be reviewed before the real execution path
  // (Recon -> Ozone's ReconfigureProtocol) is built.
  confirmApplyFix = () => {
    const { diagnosis, alert } = this.state;
    if (!diagnosis) {
      return;
    }
    const preview = this.resolveRemediationPreview(diagnosis, alert);
    const configChanges = preview.config_changes;
    Modal.confirm({
      title: 'Apply this fix to the cluster?',
      icon: <ExclamationCircleOutlined />,
      width: 520,
      content: (
        <div>
          <p>{preview.summary}</p>
          <ul>
            {Object.entries(configChanges).map(([key, value]) =>
              <li key={key}><code>{key}</code> = <code>{value}</code></li>)}
          </ul>
          <Alert
            type='warning'
            showIcon
            message='Preview only'
            description='Automatic cluster mutation is not implemented yet. Nothing on the cluster will actually change; this simulates what the applied result will look like.'
          />
        </div>
      ),
      okText: 'Apply (preview)',
      onOk: this.simulateApplyFix
    });
  };

  simulateApplyFix = () => {
    const { diagnosis, alert, remediation } = this.state;
    if (!diagnosis) {
      return;
    }
    const preview = this.resolveRemediationPreview(diagnosis, alert);
    this.setState({ applying: true, applyResult: undefined });
    setTimeout(() => {
      this.setState({
        applying: false,
        applyResult: {
          status: 'success',
          appliedAt: moment().toISOString(),
          configChanges: remediation?.config_changes ?? preview.config_changes
        }
      });
    }, 1200);
  };

  renderAlertSummary = (alert: IStoredAlert) => {
    const severity = alert.labels.severity ?? 'unknown';
    return (
      <Card className='alert-summary-card'>
        <h2 className='alert-diagnose-title'>{alert.labels.alertname ?? 'Unknown alert'}</h2>
        <p>{alert.annotations.summary ?? alert.annotations.description ?? 'No summary available.'}</p>
        <div className='alert-summary-meta'>
          <Tag color={alert.state === 'firing' ? 'red' : 'gold'}>{alert.state}</Tag>
          <Tag color={SEVERITY_COLORS[severity] ?? 'default'}>{severity}</Tag>
          {alert.labels.component &&
            <Tag>{alert.labels.component.toUpperCase()}</Tag>}
          {alert.labels.instance &&
            <Tag>{alert.labels.instance}</Tag>}
          {alert.activeAt &&
            <Tag>Active since {moment(alert.activeAt).format('lll')}</Tag>}
        </div>
      </Card>
    );
  };

  renderVerdictBanner = (diagnosis: IDiagnosisResponse) => {
    if (diagnosis.alert_confirmed) {
      return (
        <Alert
          type='success'
          showIcon
          icon={<CheckCircleOutlined />}
          message='Alert confirmed by live metrics'
          description={diagnosis.verdict_reason || 'Live metrics match the alert condition.'}
          style={{ marginBottom: 20 }}
        />
      );
    }
    return (
      <Alert
        type='warning'
        showIcon
        icon={<WarningOutlined />}
        message='Alert could not be confirmed against current metrics'
        description={`${diagnosis.verdict_reason || ''} Review the analysis below before applying any fix.`}
        style={{ marginBottom: 20 }}
      />
    );
  };

  renderAnalysisSection = (diagnosis: IDiagnosisResponse) => {
    const what = diagnosis.what_happened || diagnosis.diagnosis || 'No analysis available.';
    const why = diagnosis.why_it_happened || 'Root cause could not be determined from available evidence.';
    const how = diagnosis.how_to_fix
      || diagnosis.recommended_fix?.summary
      || 'Review evidence and runbooks for manual remediation steps.';

    return (
      <div className='analysis-grid'>
        <Card className='analysis-card analysis-card-what' bodyStyle={{ padding: 0 }}>
          <div className='analysis-card-header'>
            <QuestionCircleOutlined className='analysis-icon' />
            <h3 className='analysis-heading'>What happened?</h3>
          </div>
          <div className='analysis-card-body'>{what}</div>
        </Card>
        <Card className='analysis-card analysis-card-why' bodyStyle={{ padding: 0 }}>
          <div className='analysis-card-header'>
            <BulbOutlined className='analysis-icon' />
            <h3 className='analysis-heading'>Why did it happen?</h3>
          </div>
          <div className='analysis-card-body'>{why}</div>
        </Card>
        <Card className='analysis-card analysis-card-fix' bodyStyle={{ padding: 0 }}>
          <div className='analysis-card-header'>
            <ToolOutlined className='analysis-icon' />
            <h3 className='analysis-heading'>How to fix it?</h3>
          </div>
          <div className='analysis-card-body'>{how}</div>
        </Card>
      </div>
    );
  };

  render() {
    const {
      alert,
      loadingAlert,
      diagnosing,
      diagnosis,
      diagnosisError,
      remediating,
      remediation,
      remediationError,
      applying,
      applyResult
    } = this.state;

    return (
      <div className='alert-diagnose-page'>
        <div className='alert-diagnose-header'>
          <Link to='/Alerts' className='alert-diagnose-back'>
            <ArrowLeftOutlined /> Back to Alerts
          </Link>
          <Button icon={<RobotOutlined />} loading={diagnosing} onClick={this.runDiagnosis}
            disabled={loadingAlert || diagnosing}>
            Re-run analysis
          </Button>
        </div>

        {loadingAlert &&
          <div className='alert-diagnose-loading'>
            <Spin size='large' />
            <span className='loading-text'>Loading alert details…</span>
          </div>}

        {!loadingAlert && alert && this.renderAlertSummary(alert)}

        {(loadingAlert || diagnosing) && !diagnosis &&
          <div className='alert-diagnose-loading'>
            <RobotOutlined className='loading-icon' />
            <span className='loading-text'>AI is analyzing this alert…</span>
            <Spin />
          </div>}

        {diagnosisError &&
          <Alert type='error' showIcon message={diagnosisError} style={{ marginBottom: 16 }} />}

        {diagnosis && this.renderVerdictBanner(diagnosis)}

        {diagnosis && this.renderAnalysisSection(diagnosis)}

        {diagnosis && (diagnosis.evidence.length > 0 || diagnosis.retrieved_documents.length > 0) &&
          <div className='alert-diagnose-extra'>
            <Collapse>
              {diagnosis.evidence.length > 0 &&
                <Collapse.Panel header={`Evidence (${diagnosis.evidence.length})`} key='evidence'>
                  <ul>{diagnosis.evidence.map((item, idx) => <li key={idx}>{item}</li>)}</ul>
                </Collapse.Panel>}
              {diagnosis.retrieved_documents.length > 0 &&
                <Collapse.Panel header='Matched runbooks' key='runbooks'>
                  {diagnosis.retrieved_documents.map((doc, idx) =>
                    <div key={idx} style={{ marginBottom: 12 }}>
                      <Tag>{doc.source}</Tag>
                      <p style={{ marginTop: 8, marginBottom: 0 }}>{doc.snippet}</p>
                    </div>)}
                </Collapse.Panel>}
            </Collapse>
          </div>}

        {diagnosis && (() => {
          const remediationPreview = this.resolveRemediationPreview(diagnosis, alert);
          return (
            <Card className='remediation-card' title='Remediation'>
              <p>{remediationPreview.summary}</p>
              {!diagnosis.recommended_fix &&
                <Alert type='info' showIcon style={{ marginBottom: 12 }}
                  message='Using suggested fix from analysis'
                  description='The backend did not attach an automated fix (for example when the alert could not be confirmed). The action below is inferred from the alert component and How to fix guidance.' />}
              {Object.keys(remediationPreview.config_changes).length > 0 &&
                <Descriptions size='small' column={1} bordered>
                  {Object.entries(remediationPreview.config_changes).map(([key, value]) =>
                    <Descriptions.Item key={key} label={key}>{value}</Descriptions.Item>)}
                </Descriptions>}
              <div className='remediation-actions'>
                <Button type='primary' loading={remediating} onClick={this.fix} style={{ marginRight: 8 }}>
                  Remediate (dry run)
                </Button>
                <Button icon={<ThunderboltOutlined />} loading={applying} onClick={this.confirmApplyFix}>
                  Apply Fix (preview)
                </Button>
              </div>
            </Card>
          );
        })()}

        {remediationError &&
          <Alert type='error' showIcon message={remediationError} style={{ marginTop: 16 }} />}
        {remediation &&
          <Card style={{ marginTop: 16 }} title='Remediation plan (not applied)'>
            <p>{remediation.description}</p>
            {remediation.warnings.length > 0 &&
              <Alert type='warning' showIcon message={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {remediation.warnings.map((warning, idx) => <li key={idx}>{warning}</li>)}
                </ul>
              } />}
          </Card>}

        {applyResult &&
          <Card style={{ marginTop: 16 }} className='remediation-card'
            title={<span><CheckCircleOutlined style={{ color: '#4DCF4C', marginRight: 8 }} />Fix applied (preview)</span>}>
            <Descriptions size='small' column={1} bordered>
              {Object.entries(applyResult.configChanges).map(([key, value]) =>
                <Descriptions.Item key={key} label={key}>{value}</Descriptions.Item>)}
              <Descriptions.Item label='Applied at'>
                {moment(applyResult.appliedAt).format('lll')}
              </Descriptions.Item>
            </Descriptions>
            <Alert
              type='info'
              showIcon
              style={{ marginTop: 12 }}
              message='This is a UI preview only'
              description='Live cluster mutation is not implemented yet. Apply this change manually (e.g. via ozone admin reconfig) until automated execution ships.'
            />
          </Card>}
      </div>
    );
  }
}

export default AlertDiagnose;
