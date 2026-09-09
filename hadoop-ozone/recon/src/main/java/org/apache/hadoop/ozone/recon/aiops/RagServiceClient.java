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

package org.apache.hadoop.ozone.recon.aiops;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import javax.inject.Inject;
import javax.inject.Singleton;
import org.apache.commons.io.IOUtils;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.hdfs.web.URLConnectionFactory;
import org.apache.hadoop.ozone.recon.aiops.model.StoredAlert;
import org.apache.hadoop.security.authentication.client.AuthenticationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side HTTP client for recon-rag-service's diagnose/remediate APIs.
 */
@Singleton
public class RagServiceClient {

  private static final Logger LOG =
      LoggerFactory.getLogger(RagServiceClient.class);

  private final String ragServiceEndpoint;
  private final URLConnectionFactory connectionFactory;
  private final ObjectMapper objectMapper = new ObjectMapper();

  @Inject
  public RagServiceClient(OzoneConfiguration configuration) {
    String endpoint = configuration.getTrimmed(
        AIOpsConfigKeys.OZONE_RECON_AIOPS_RAG_SERVICE_ENDPOINT);
    if (endpoint != null && endpoint.endsWith("/")) {
      endpoint = endpoint.substring(0, endpoint.length() - 1);
    }
    this.ragServiceEndpoint = endpoint;
    int timeoutMs = (int) configuration.getTimeDuration(
        AIOpsConfigKeys.OZONE_RECON_AIOPS_HTTP_TIMEOUT,
        AIOpsConfigKeys.OZONE_RECON_AIOPS_HTTP_TIMEOUT_DEFAULT,
        TimeUnit.MILLISECONDS);
    this.connectionFactory = URLConnectionFactory.newDefaultURLConnectionFactory(
        timeoutMs, timeoutMs, configuration);
  }

  public boolean isConfigured() {
    return ragServiceEndpoint != null && !ragServiceEndpoint.isEmpty();
  }

  public Map<String, Object> diagnose(StoredAlert alert) throws IOException {
    return postJson("/api/v1/diagnose", toAlertPayload(alert));
  }

  public Map<String, Object> remediate(StoredAlert alert, String actionId,
      boolean dryRun) throws IOException {
    Map<String, Object> body = new HashMap<>();
    body.put("alert", toAlertPayload(alert));
    body.put("action_id", actionId);
    return postJson("/api/v1/remediate?dryRun=" + dryRun, body);
  }

  private Map<String, Object> toAlertPayload(StoredAlert alert) {
    Map<String, Object> payload = new HashMap<>();
    payload.put("labels", alert.getLabels());
    payload.put("annotations", alert.getAnnotations());
    payload.put("state", alert.getState());
    payload.put("activeAt", alert.getActiveAt());
    return payload;
  }

  @SuppressWarnings("unchecked")
  private Map<String, Object> postJson(String path, Object body)
      throws IOException {
    if (!isConfigured()) {
      throw new IOException("ozone.recon.aiops.rag-service.endpoint is not configured");
    }
    URL url = new URL(ragServiceEndpoint + path);
    HttpURLConnection connection;
    try {
      connection =
          (HttpURLConnection) connectionFactory.openConnection(url, false);
    } catch (AuthenticationException e) {
      throw new IOException("Failed to open connection to recon-rag-service", e);
    }
    connection.setRequestMethod("POST");
    connection.setRequestProperty("Content-Type", "application/json");
    connection.setDoOutput(true);
    byte[] payload = objectMapper.writeValueAsBytes(body);
    try (OutputStream outputStream = connection.getOutputStream()) {
      outputStream.write(payload);
    }
    int responseCode = connection.getResponseCode();
    InputStream inputStream = responseCode >= 400
        ? connection.getErrorStream()
        : connection.getInputStream();
    String responseBody = inputStream == null ? ""
        : IOUtils.toString(inputStream, StandardCharsets.UTF_8);
    if (responseCode >= 400) {
      LOG.warn("recon-rag-service {} returned HTTP {}: {}", path, responseCode,
          responseBody);
      throw new IOException("recon-rag-service call failed with HTTP "
          + responseCode + ": " + responseBody);
    }
    if (responseBody.isEmpty()) {
      return new HashMap<>();
    }
    return objectMapper.readValue(responseBody, Map.class);
  }
}
