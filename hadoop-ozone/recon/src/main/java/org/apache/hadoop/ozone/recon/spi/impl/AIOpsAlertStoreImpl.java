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

package org.apache.hadoop.ozone.recon.spi.impl;

import static org.apache.hadoop.ozone.recon.spi.impl.ReconDBDefinition.AIOPS_ALERTS;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import javax.inject.Inject;
import javax.inject.Singleton;
import org.apache.hadoop.hdds.utils.db.Table;
import org.apache.hadoop.hdds.utils.db.TableIterator;
import org.apache.hadoop.ozone.recon.aiops.model.StoredAlert;
import org.apache.hadoop.ozone.recon.spi.AIOpsAlertStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Persists alert state in the {@code aiopsAlertsTable} RocksDB column family.
 */
@Singleton
public class AIOpsAlertStoreImpl implements AIOpsAlertStore {

  private static final Logger LOG =
      LoggerFactory.getLogger(AIOpsAlertStoreImpl.class);

  private final ObjectMapper objectMapper = new ObjectMapper();
  private Table<String, String> alertTable;
  private ReconDBProvider reconDBProvider;

  @Inject
  public AIOpsAlertStoreImpl(ReconDBProvider reconDBProvider) {
    this.reconDBProvider = reconDBProvider;
    reinitialize(reconDBProvider);
  }

  @Override
  public void reinitialize(ReconDBProvider provider) {
    this.reconDBProvider = provider;
    initializeTable();
  }

  private void initializeTable() {
    try {
      alertTable = AIOPS_ALERTS.getTable(reconDBProvider.getDbStore());
    } catch (IOException e) {
      LOG.error("Unable to open AIOps alerts table.", e);
    }
  }

  @Override
  public void upsert(StoredAlert alert) throws IOException {
    if (alertTable == null) {
      throw new IOException("AIOps alerts table is not initialized");
    }
    alertTable.put(alert.getId(), serialize(alert));
  }

  @Override
  public StoredAlert get(String alertId) throws IOException {
    if (alertTable == null) {
      throw new IOException("AIOps alerts table is not initialized");
    }
    String json = alertTable.get(alertId);
    if (json == null) {
      return null;
    }
    return deserialize(json);
  }

  @Override
  public List<StoredAlert> listAlerts() throws IOException {
    if (alertTable == null) {
      throw new IOException("AIOps alerts table is not initialized");
    }
    List<StoredAlert> alerts = new ArrayList<>();
    try (TableIterator<String, Table.KeyValue<String, String>> iterator =
        alertTable.iterator()) {
      while (iterator.hasNext()) {
        Table.KeyValue<String, String> entry = iterator.next();
        alerts.add(deserialize(entry.getValue()));
      }
    }
    alerts.sort(Comparator.comparingLong(StoredAlert::getUpdatedAtMs).reversed());
    return alerts;
  }

  private String serialize(StoredAlert alert) throws JsonProcessingException {
    return objectMapper.writeValueAsString(alert);
  }

  private StoredAlert deserialize(String json) throws IOException {
    return objectMapper.readValue(json, StoredAlert.class);
  }
}
