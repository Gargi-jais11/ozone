/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements. See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.hadoop.ozone.recon.api;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.inject.Inject;
import javax.ws.rs.Consumes;
import javax.ws.rs.DefaultValue;
import javax.ws.rs.GET;
import javax.ws.rs.NotFoundException;
import javax.ws.rs.POST;
import javax.ws.rs.Path;
import javax.ws.rs.PathParam;
import javax.ws.rs.Produces;
import javax.ws.rs.QueryParam;
import javax.ws.rs.core.MediaType;
import javax.ws.rs.core.Response;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.ozone.recon.aiops.AIOpsConfigKeys;
import org.apache.hadoop.ozone.recon.aiops.AlertFingerprintUtil;
import org.apache.hadoop.ozone.recon.aiops.RagServiceClient;
import org.apache.hadoop.ozone.recon.aiops.model.StoredAlert;
import org.apache.hadoop.ozone.recon.spi.AIOpsAlertStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Recon REST surface for Alertmanager webhook ingestion, alert listing, and
 * server-side calls to recon-rag-service.
 */
@Path("/aiops")
@Produces(MediaType.APPLICATION_JSON)
public class AIOpsEndpoint {

  private static final Logger LOG =
      LoggerFactory.getLogger(AIOpsEndpoint.class);

  private final OzoneConfiguration configuration;
  private final AIOpsAlertStore alertStore;
  private final RagServiceClient ragServiceClient;
  private final ObjectMapper objectMapper = new ObjectMapper();

  @Inject
  public AIOpsEndpoint(OzoneConfiguration configuration,
      AIOpsAlertStore alertStore,
      RagServiceClient ragServiceClient) {
    this.configuration = configuration;
    this.alertStore = alertStore;
    this.ragServiceClient = ragServiceClient;
  }

  @POST
  @Path("/webhook")
  @Consumes(MediaType.APPLICATION_JSON)
  public Response receiveWebhook(InputStream payload) {
    if (!AIOpsConfigKeys.isAIOpsEnabled(configuration)) {
      return Response.status(Response.Status.SERVICE_UNAVAILABLE)
          .entity(errorBody("AIOps is disabled"))
          .build();
    }
    try {
      AlertmanagerWebhook webhook =
          objectMapper.readValue(payload, AlertmanagerWebhook.class);
      if (webhook.getAlerts() == null) {
        return Response.ok().build();
      }
      long now = System.currentTimeMillis();
      for (AlertmanagerAlert alert : webhook.getAlerts()) {
        StoredAlert storedAlert = toStoredAlert(alert, now);
        alertStore.upsert(storedAlert);
      }
      return Response.ok().build();
    } catch (IOException e) {
      LOG.error("Failed to process Alertmanager webhook", e);
      return Response.status(Response.Status.BAD_REQUEST)
          .entity(errorBody(e.getMessage()))
          .build();
    }
  }

  @GET
  @Path("/alerts")
  public Response listAlerts() {
    if (!AIOpsConfigKeys.isAIOpsEnabled(configuration)) {
      return Response.status(Response.Status.SERVICE_UNAVAILABLE)
          .entity(errorBody("AIOps is disabled"))
          .build();
    }
    try {
      Map<String, Object> response = new HashMap<>();
      response.put("alerts", alertStore.listAlerts());
      return Response.ok(response).build();
    } catch (IOException e) {
      LOG.error("Failed to list AIOps alerts", e);
      return Response.status(Response.Status.INTERNAL_SERVER_ERROR)
          .entity(errorBody(e.getMessage()))
          .build();
    }
  }

  @POST
  @Path("/alerts/{alertId}/diagnose")
  public Response diagnose(@PathParam("alertId") String alertId) {
    if (!AIOpsConfigKeys.isAIOpsEnabled(configuration)) {
      return Response.status(Response.Status.SERVICE_UNAVAILABLE)
          .entity(errorBody("AIOps is disabled"))
          .build();
    }
    if (!ragServiceClient.isConfigured()) {
      return Response.status(Response.Status.NOT_IMPLEMENTED)
          .entity(errorBody("ozone.recon.aiops.rag-service.endpoint is not configured"))
          .build();
    }
    try {
      StoredAlert alert = alertStore.get(alertId);
      if (alert == null) {
        throw new NotFoundException("Alert not found: " + alertId);
      }
      return Response.ok(ragServiceClient.diagnose(alert)).build();
    } catch (NotFoundException e) {
      return Response.status(Response.Status.NOT_FOUND)
          .entity(errorBody(e.getMessage()))
          .build();
    } catch (IOException e) {
      LOG.error("Failed to diagnose alert {}", alertId, e);
      return Response.status(Response.Status.BAD_GATEWAY)
          .entity(errorBody(e.getMessage()))
          .build();
    }
  }

  @POST
  @Path("/alerts/{alertId}/remediate")
  public Response remediate(@PathParam("alertId") String alertId,
      @QueryParam("actionId") String actionId,
      @DefaultValue("true") @QueryParam("dryRun") boolean dryRun) {
    if (!AIOpsConfigKeys.isAIOpsEnabled(configuration)) {
      return Response.status(Response.Status.SERVICE_UNAVAILABLE)
          .entity(errorBody("AIOps is disabled"))
          .build();
    }
    if (!ragServiceClient.isConfigured()) {
      return Response.status(Response.Status.NOT_IMPLEMENTED)
          .entity(errorBody("ozone.recon.aiops.rag-service.endpoint is not configured"))
          .build();
    }
    if (actionId == null || actionId.isEmpty()) {
      return Response.status(Response.Status.BAD_REQUEST)
          .entity(errorBody("actionId query parameter is required"))
          .build();
    }
    try {
      StoredAlert alert = alertStore.get(alertId);
      if (alert == null) {
        throw new NotFoundException("Alert not found: " + alertId);
      }
      return Response.ok(ragServiceClient.remediate(alert, actionId, dryRun)).build();
    } catch (NotFoundException e) {
      return Response.status(Response.Status.NOT_FOUND)
          .entity(errorBody(e.getMessage()))
          .build();
    } catch (IOException e) {
      LOG.error("Failed to remediate alert {}", alertId, e);
      return Response.status(Response.Status.BAD_GATEWAY)
          .entity(errorBody(e.getMessage()))
          .build();
    }
  }

  private StoredAlert toStoredAlert(AlertmanagerAlert alert, long updatedAtMs) {
    StoredAlert storedAlert = new StoredAlert();
    Map<String, String> labels = alert.getLabels() == null
        ? new HashMap<>()
        : new HashMap<>(alert.getLabels());
    Map<String, String> annotations = alert.getAnnotations() == null
        ? new HashMap<>()
        : new HashMap<>(alert.getAnnotations());
    storedAlert.setLabels(labels);
    storedAlert.setAnnotations(annotations);
    storedAlert.setId(AlertFingerprintUtil.fingerprint(labels));
    storedAlert.setState(alert.getStatus());
    storedAlert.setActiveAt(alert.getStartsAt());
    storedAlert.setUpdatedAtMs(updatedAtMs);
    return storedAlert;
  }

  private Map<String, String> errorBody(String message) {
    Map<String, String> body = new HashMap<>();
    body.put("message", message);
    return body;
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  private static final class AlertmanagerWebhook {
    private List<AlertmanagerAlert> alerts;

    public List<AlertmanagerAlert> getAlerts() {
      return alerts;
    }

    public void setAlerts(List<AlertmanagerAlert> alerts) {
      this.alerts = alerts;
    }
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  private static final class AlertmanagerAlert {
    private String status;
    private Map<String, String> labels;
    private Map<String, String> annotations;
    private String startsAt;
    private String endsAt;

    public String getStatus() {
      return status;
    }

    public void setStatus(String status) {
      this.status = status;
    }

    public Map<String, String> getLabels() {
      return labels;
    }

    public void setLabels(Map<String, String> labels) {
      this.labels = labels;
    }

    public Map<String, String> getAnnotations() {
      return annotations;
    }

    public void setAnnotations(Map<String, String> annotations) {
      this.annotations = annotations;
    }

    public String getStartsAt() {
      return startsAt;
    }

    public void setStartsAt(String startsAt) {
      this.startsAt = startsAt;
    }

    public String getEndsAt() {
      return endsAt;
    }

    public void setEndsAt(String endsAt) {
      this.endsAt = endsAt;
    }
  }
}
