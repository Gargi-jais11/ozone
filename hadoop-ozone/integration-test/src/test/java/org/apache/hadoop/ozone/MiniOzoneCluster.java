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

package org.apache.hadoop.ozone;

import com.amazonaws.services.s3.AmazonS3;
import java.io.IOException;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeoutException;
import org.apache.hadoop.hdds.HddsConfigKeys;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.hdds.protocol.DatanodeDetails;
import org.apache.hadoop.hdds.protocol.proto.HddsProtos;
import org.apache.hadoop.hdds.scm.protocolPB.StorageContainerLocationProtocolClientSideTranslatorPB;
import org.apache.hadoop.hdds.scm.server.SCMConfigurator;
import org.apache.hadoop.hdds.scm.server.StorageContainerManager;
import org.apache.hadoop.hdds.security.symmetric.SecretKeyClient;
import org.apache.hadoop.hdds.security.x509.certificate.client.CertificateClient;
import org.apache.hadoop.ozone.client.OzoneClient;
import org.apache.hadoop.ozone.om.OzoneManager;
import org.apache.hadoop.ozone.recon.ReconServer;
import org.apache.hadoop.ozone.s3.Gateway;
import org.apache.hadoop.security.authentication.client.AuthenticationException;
import org.apache.ozone.test.GenericTestUtils;
import org.apache.ratis.util.ExitUtils;
import org.apache.ratis.util.function.CheckedFunction;
import software.amazon.awssdk.services.s3.S3Client;

/**
 * Interface used for MiniOzoneClusters.
 */
public interface MiniOzoneCluster extends AutoCloseable {

  /**
   * Returns the Builder to construct MiniOzoneCluster.
   *
   * @param conf OzoneConfiguration
   *
   * @return MiniOzoneCluster builder
   */
  static Builder newBuilder(OzoneConfiguration conf) {
    return new MiniOzoneClusterImpl.Builder(conf);
  }

  /**
   * Returns the Builder to construct MiniOzoneHACluster.
   *
   * @param conf OzoneConfiguration
   *
   * @return MiniOzoneCluster builder
   */
  static MiniOzoneHAClusterImpl.Builder newHABuilder(OzoneConfiguration conf) {
    return new MiniOzoneHAClusterImpl.Builder(conf);
  }

  /**
   * Returns the configuration object associated with the MiniOzoneCluster.
   *
   * @return Configuration
   */
  OzoneConfiguration getConf();

  /**
   * Waits for the cluster to be ready, this call blocks till all the
   * configured {@link HddsDatanodeService} registers with
   * {@link StorageContainerManager}.
   *
   * @throws TimeoutException In case of timeout
   * @throws InterruptedException In case of interrupt while waiting
   */
  void waitForClusterToBeReady() throws TimeoutException, InterruptedException;

  /**
   * Waits for at least one RATIS pipeline of given factor to be reported in open
   * state.
   *
   * @param factor replication factor
   * @param timeoutInMs timeout value in milliseconds
   * @throws TimeoutException In case of timeout
   * @throws InterruptedException In case of interrupt while waiting
   */
  void waitForPipelineTobeReady(HddsProtos.ReplicationFactor factor,
                                int timeoutInMs)
          throws TimeoutException, InterruptedException;

  /**
   * Sets the timeout value after which
   * {@link MiniOzoneCluster#waitForClusterToBeReady} times out.
   *
   * @param timeoutInMs timeout value in milliseconds
   */
  void setWaitForClusterToBeReadyTimeout(int timeoutInMs);

  /**
   * Waits/blocks till the cluster is out of safe mode.
   *
   * @throws TimeoutException TimeoutException In case of timeout
   * @throws InterruptedException In case of interrupt while waiting
   */
  void waitTobeOutOfSafeMode() throws TimeoutException, InterruptedException;

  /**
   * Returns {@link StorageContainerManager} associated with this
   * {@link MiniOzoneCluster} instance.
   *
   * @return {@link StorageContainerManager} instance
   */
  StorageContainerManager getStorageContainerManager();

  /**
   * Returns {@link OzoneManager} associated with this
   * {@link MiniOzoneCluster} instance.
   *
   * @return {@link OzoneManager} instance
   */
  OzoneManager getOzoneManager();

  /**
   * Returns the list of {@link HddsDatanodeService} which are part of this
   * {@link MiniOzoneCluster} instance.
   *
   * @return List of {@link HddsDatanodeService}
   */
  List<HddsDatanodeService> getHddsDatanodes();

  HddsDatanodeService getHddsDatanode(DatanodeDetails dn) throws IOException;

  /**
   * Returns a {@link ReconServer} instance.
   *
   * @return {@link ReconServer} instance if it is initialized, otherwise null.
   */
  ReconServer getReconServer();

  /**
   * Returns a {@link Gateway} instance.
   *
   * @return {@link Gateway} instance if it is initialized, otherwise null.
   */
  Gateway getS3G();

  /**
   * Returns an {@link OzoneClient} to access the {@link MiniOzoneCluster}.
   * The caller is responsible for closing the client after use.
   *
   * @return {@link OzoneClient}
   */
  OzoneClient newClient() throws IOException;

  /**
   * Returns an {@link AmazonS3} to use AWS SDK V1 to access the {@link MiniOzoneCluster}.
   */
  AmazonS3 newS3Client();

  /**
   * Returns an {@link S3Client} to use AWS SDK V2 to access the {@link MiniOzoneCluster}.
   */
  S3Client newS3ClientV2() throws Exception;

  /**
   * Returns StorageContainerLocationClient to communicate with
   * {@link StorageContainerManager} associated with the MiniOzoneCluster.
   */
  StorageContainerLocationProtocolClientSideTranslatorPB
      getStorageContainerLocationClient() throws IOException;

  /**
   * Restarts StorageContainerManager instance.
   */
  void restartStorageContainerManager(boolean waitForDatanode)
      throws InterruptedException, TimeoutException, IOException,
      AuthenticationException;

  /**
   * Restarts OzoneManager instance.
   */
  void restartOzoneManager() throws IOException;

  /**
   * Restarts Recon instance.
   */
  void restartReconServer() throws Exception;

  /**
   * Restart a particular HddsDatanode.
   *
   * @param i index of HddsDatanode in the MiniOzoneCluster
   */
  void restartHddsDatanode(int i, boolean waitForDatanode)
      throws InterruptedException, TimeoutException;

  int getHddsDatanodeIndex(DatanodeDetails dn) throws IOException;

  /**
   * Restart a particular HddsDatanode.
   *
   * @param dn HddsDatanode in the MiniOzoneCluster
   */
  void restartHddsDatanode(DatanodeDetails dn, boolean waitForDatanode)
      throws InterruptedException, TimeoutException, IOException;
  /**
   * Shutdown a particular HddsDatanode.
   *
   * @param i index of HddsDatanode in the MiniOzoneCluster
   */
  void shutdownHddsDatanode(int i);

  /**
   * Shutdown a particular HddsDatanode.
   *
   * @param dn HddsDatanode in the MiniOzoneCluster
   */
  void shutdownHddsDatanode(DatanodeDetails dn) throws IOException;

  /**
   * Start Recon.
   */
  void startRecon();

  /**
   * Stop Recon.
   */
  void stopRecon();

  /**
   * Start S3G.
   */
  void startS3G();

  /**
   * Restart S3G.
   */
  void restartS3G();

  /**
   * Stop S3G.
   */
  void stopS3G();

  /**
   * Shutdown the MiniOzoneCluster and delete the storage dirs.
   */
  void shutdown();

  @Override
  default void close() {
    shutdown();
  }

  /**
   * Stop the MiniOzoneCluster without any cleanup.
   */
  void stop();

  /**
   * Start DataNodes.
   */
  void startHddsDatanodes();

  /**
   * Shuts down all the DataNodes.
   */
  void shutdownHddsDatanodes();

  String getClusterId();

  default String getName() {
    return getClass().getSimpleName() + "-" + getClusterId();
  }

  default String getBaseDir() {
    return GenericTestUtils.getTempPath(getName());
  }

  /**
   * Builder class for MiniOzoneCluster.
   */
  @SuppressWarnings("visibilitymodifier")
  abstract class Builder {

    protected static final int ACTIVE_OMS_NOT_SET = -1;
    protected static final int ACTIVE_SCMS_NOT_SET = -1;
    protected static final int DEFAULT_RATIS_RPC_TIMEOUT_SEC = 1;

    protected OzoneConfiguration conf;
    protected String path;

    protected String clusterId;
    protected SCMConfigurator scmConfigurator;

    protected String scmId = UUID.randomUUID().toString();
    protected String omId = UUID.randomUUID().toString();

    protected boolean includeRecon = false;
    protected boolean includeS3G = false;

    protected int numOfDatanodes = 3;
    protected boolean  startDataNodes = true;
    protected CertificateClient certClient;
    protected SecretKeyClient secretKeyClient;
    protected DatanodeFactory dnFactory = UniformDatanodesFactory.newBuilder().build();

    protected Builder(OzoneConfiguration conf) {
      this.conf = conf;
      setClusterId();
      // Use default SCM configurations if no override is provided.
      setSCMConfigurator(new SCMConfigurator());
      ExitUtils.disableSystemExit();
    }

    /** Prepare the builder for another call to {@link #build()}, avoiding conflict
     * between the clusters created. */
    protected void prepareForNextBuild() {
      conf = new OzoneConfiguration(conf);
      setClusterId();
    }

    public Builder setSCMConfigurator(SCMConfigurator configurator) {
      this.scmConfigurator = configurator;
      return this;
    }

    private void setClusterId() {
      clusterId = UUID.randomUUID().toString();
      path = GenericTestUtils.getTempPath(
          MiniOzoneClusterImpl.class.getSimpleName() + "-" + clusterId);
    }

    /**
     * For tests that do not use any features of SCM, we can get by with
     * 0 datanodes.  Also need to skip safemode in this case.
     * This allows the cluster to come up much faster.
     */
    public Builder withoutDatanodes() {
      setNumDatanodes(0);
      conf.setBoolean(HddsConfigKeys.HDDS_SCM_SAFEMODE_ENABLED, false);
      return this;
    }

    public Builder setStartDataNodes(boolean nodes) {
      this.startDataNodes = nodes;
      return this;
    }

    public Builder setCertificateClient(CertificateClient client) {
      this.certClient = client;
      return this;
    }

    public Builder setSecretKeyClient(SecretKeyClient client) {
      this.secretKeyClient = client;
      return this;
    }

    /**
     * Sets the number of HddsDatanodes to be started as part of
     * MiniOzoneCluster.
     *
     * @param val number of datanodes
     *
     * @return MiniOzoneCluster.Builder
     */
    public Builder setNumDatanodes(int val) {
      numOfDatanodes = val;
      return this;
    }

    public Builder setDatanodeFactory(DatanodeFactory factory) {
      this.dnFactory = factory;
      return this;
    }

    public Builder includeRecon(boolean include) {
      this.includeRecon = include;
      return this;
    }

    public Builder includeS3G(boolean include) {
      this.includeS3G = include;
      return this;
    }

    /**
     * Constructs and returns MiniOzoneCluster.
     *
     * @return {@link MiniOzoneCluster}
     */
    public abstract MiniOzoneCluster build() throws IOException;
  }

  /**
   * Factory to customize configuration of each datanode.
   */
  interface DatanodeFactory extends CheckedFunction<OzoneConfiguration, OzoneConfiguration, IOException> {
    // marker
  }
}
