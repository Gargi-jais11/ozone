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

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Collections;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;

/**
 * Derives a stable alert id from an Alertmanager/Prometheus label set.
 */
public final class AlertFingerprintUtil {

  /** Labels excluded from the fingerprint (severity escalates in-place). */
  private static final Set<String> EXCLUDED_LABELS =
      Collections.singleton("severity");

  private AlertFingerprintUtil() {
  }

  public static String fingerprint(Map<String, String> labels) {
    if (labels == null || labels.isEmpty()) {
      return "unknown";
    }
    TreeMap<String, String> sorted = new TreeMap<>();
    for (Map.Entry<String, String> entry : labels.entrySet()) {
      if (!EXCLUDED_LABELS.contains(entry.getKey())) {
        sorted.put(entry.getKey(), entry.getValue());
      }
    }
    if (sorted.isEmpty()) {
      return "unknown";
    }
    String canonical = sorted.entrySet().stream()
        .map(entry -> entry.getKey() + "=" + entry.getValue())
        .collect(Collectors.joining(","));
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      byte[] hash = digest.digest(canonical.getBytes(StandardCharsets.UTF_8));
      StringBuilder builder = new StringBuilder(hash.length * 2);
      for (byte value : hash) {
        builder.append(String.format("%02x", value));
      }
      return builder.toString();
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 not available", e);
    }
  }
}
