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

import org.apache.hadoop.hdds.conf.OzoneConfiguration;

/**
 * Configuration keys for the Recon AIOps alert ingestion and diagnosis feature.
 */
public final class AIOpsConfigKeys {

  public static final String OZONE_RECON_AIOPS_ENABLED =
      "ozone.recon.aiops.enabled";
  public static final boolean OZONE_RECON_AIOPS_ENABLED_DEFAULT = true;

  public static final String OZONE_RECON_AIOPS_RAG_SERVICE_ENDPOINT =
      "ozone.recon.aiops.rag-service.endpoint";

  public static final String OZONE_RECON_AIOPS_HTTP_TIMEOUT =
      "ozone.recon.aiops.http.timeout";
  public static final String OZONE_RECON_AIOPS_HTTP_TIMEOUT_DEFAULT = "30s";

  private AIOpsConfigKeys() {
  }

  public static boolean isAIOpsEnabled(OzoneConfiguration configuration) {
    return configuration.getBoolean(OZONE_RECON_AIOPS_ENABLED,
        OZONE_RECON_AIOPS_ENABLED_DEFAULT);
  }
}
