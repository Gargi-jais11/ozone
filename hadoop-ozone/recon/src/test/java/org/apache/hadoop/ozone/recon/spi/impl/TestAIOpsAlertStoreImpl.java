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

import static org.apache.hadoop.ozone.recon.ReconServerConfigKeys.OZONE_RECON_DB_DIR;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import com.google.inject.AbstractModule;
import com.google.inject.Guice;
import com.google.inject.Injector;
import com.google.inject.Singleton;
import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.ozone.recon.ReconUtils;
import org.apache.hadoop.ozone.recon.aiops.AIOpsAlertStates;
import org.apache.hadoop.ozone.recon.aiops.model.StoredAlert;
import org.apache.hadoop.ozone.recon.spi.AIOpsAlertStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Tests for {@link AIOpsAlertStoreImpl}.
 */
public class TestAIOpsAlertStoreImpl {

  @TempDir
  private Path temporaryFolder;

  private AIOpsAlertStore alertStore;

  @BeforeEach
  public void setUp() throws IOException {
    File dbDir = temporaryFolder.toFile();
    OzoneConfiguration configuration = new OzoneConfiguration();
    configuration.set(OZONE_RECON_DB_DIR, dbDir.getAbsolutePath());
    Injector injector = Guice.createInjector(new AbstractModule() {
      @Override
      protected void configure() {
        bind(OzoneConfiguration.class).toInstance(configuration);
        bind(ReconUtils.class).in(Singleton.class);
        bind(ReconDBProvider.class).in(Singleton.class);
        bind(AIOpsAlertStore.class).to(AIOpsAlertStoreImpl.class).in(Singleton.class);
      }
    });
    alertStore = injector.getInstance(AIOpsAlertStore.class);
  }

  @Test
  public void testListAlertsExcludesResolvedByDefault() throws IOException {
    StoredAlert firing = newAlert("firing-id", AIOpsAlertStates.FIRING, 1);
    StoredAlert resolved = newAlert("resolved-id", AIOpsAlertStates.RESOLVED, 2);
    alertStore.upsert(firing);
    alertStore.upsert(resolved);

    assertEquals(1, alertStore.listAlerts(false).size());
    assertEquals(2, alertStore.listAlerts(true).size());
  }

  @Test
  public void testDeleteRemovesAlert() throws IOException {
    StoredAlert firing = newAlert("firing-id", AIOpsAlertStates.FIRING, 1);
    alertStore.upsert(firing);
    assertEquals(1, alertStore.listAlerts(false).size());

    alertStore.delete("firing-id");
    assertEquals(0, alertStore.listAlerts(false).size());
    assertNull(alertStore.get("firing-id"));
  }

  private static StoredAlert newAlert(String id, String state, long updatedAtMs) {
    StoredAlert alert = new StoredAlert();
    alert.setId(id);
    alert.setState(state);
    alert.setUpdatedAtMs(updatedAtMs);
    return alert;
  }
}
