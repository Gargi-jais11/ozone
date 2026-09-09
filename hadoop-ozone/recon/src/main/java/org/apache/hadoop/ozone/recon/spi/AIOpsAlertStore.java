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

package org.apache.hadoop.ozone.recon.spi;

import java.io.IOException;
import java.util.List;
import org.apache.hadoop.ozone.recon.aiops.model.StoredAlert;
import org.apache.hadoop.ozone.recon.spi.impl.ReconDBProvider;

/**
 * RocksDB-backed store for Alertmanager-delivered alert state.
 */
public interface AIOpsAlertStore {

  void upsert(StoredAlert alert) throws IOException;

  StoredAlert get(String alertId) throws IOException;

  void delete(String alertId) throws IOException;

  /**
   * @param includeResolved when false, omit alerts whose state is {@code resolved}
   */
  List<StoredAlert> listAlerts(boolean includeResolved) throws IOException;

  /**
   * Rebind table handles after {@link ReconDBProvider#replaceStagedDb}.
   *
   * @param reconDBProvider recon DB provider to reinitialize with
   */
  void reinitialize(ReconDBProvider reconDBProvider);
}
