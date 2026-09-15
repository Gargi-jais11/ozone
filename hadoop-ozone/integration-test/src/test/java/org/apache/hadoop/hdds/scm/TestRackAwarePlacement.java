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

package org.apache.hadoop.hdds.scm;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.apache.hadoop.hdds.client.RatisReplicationConfig;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.hdds.protocol.DatanodeDetails;
import org.apache.hadoop.hdds.protocol.proto.HddsProtos;
import org.apache.hadoop.hdds.protocol.proto.HddsProtos.ReplicationFactor;
import org.apache.hadoop.hdds.protocol.proto.StorageContainerDatanodeProtocolProtos.MetadataStorageReportProto;
import org.apache.hadoop.hdds.protocol.proto.StorageContainerDatanodeProtocolProtos.StorageReportProto;
import org.apache.hadoop.hdds.scm.container.ContainerID;
import org.apache.hadoop.hdds.scm.container.ContainerInfo;
import org.apache.hadoop.hdds.scm.container.ContainerReplica;
import org.apache.hadoop.hdds.scm.node.DatanodeInfo;
import org.apache.hadoop.hdds.scm.node.NodeManager;
import org.apache.hadoop.hdds.scm.pipeline.Pipeline;
import org.apache.hadoop.hdds.scm.server.StorageContainerManager;
import org.apache.hadoop.net.NetworkTopology;
import org.apache.hadoop.net.StaticMapping;
import org.apache.hadoop.ozone.MiniOzoneCluster;
import org.apache.hadoop.ozone.client.ObjectStore;
import org.apache.hadoop.ozone.client.OzoneBucket;
import org.apache.hadoop.ozone.client.OzoneClient;
import org.apache.hadoop.ozone.client.OzoneVolume;
import org.apache.hadoop.ozone.client.io.OzoneOutputStream;
import org.apache.ozone.test.GenericTestUtils;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * Integration tests that verify rack/host topology is correctly propagated
 * to SCM and that pipeline and container placement respect rack boundaries
 * and rack-capacity-weighted cross-rack selection.
 */
@Timeout(300)
public class TestRackAwarePlacement {

  private static final String RACK_AWARE_POLICY =
      "org.apache.hadoop.hdds.scm.container.placement.algorithms"
          + ".SCMContainerPlacementRackAware";
  private static final String RACK_SCATTER_POLICY =
      "org.apache.hadoop.hdds.scm.container.placement.algorithms"
          + ".SCMContainerPlacementRackScatter";

  private static final String RACK0 = "/rack0";
  private static final String RACK1 = "/rack1";
  private static final String RACK2 = "/rack2";

  private static final Topology TWO_RACK_TOPOLOGY = new Topology(
      new String[] {RACK0, RACK0, RACK0, RACK1, RACK1, RACK1},
      new String[] {
          "host0.test", "host1.test", "host2.test",
          "host3.test", "host4.test", "host5.test"
      });
  private static final Topology THREE_RACK_TOPOLOGY = new Topology(
      new String[] {RACK0, RACK0, RACK1, RACK1, RACK2, RACK2},
      new String[] {
          "host-r0-d0.test", "host-r0-d1.test",
          "host-r1-d0.test", "host-r1-d1.test",
          "host-r2-d0.test", "host-r2-d1.test"
      });

  /** A single rack with 3 datanodes carrying different remaining capacities. */
  private static final Topology SINGLE_RACK_THREE_DN_TOPOLOGY = new Topology(
      new String[] {RACK0, RACK0, RACK0},
      new String[] {
          "host-r0-d0.test", "host-r0-d1.test", "host-r0-d2.test"
      });

  /** Volume capacity used when patching SCM storage reports in capacity tests. */
  private static final long TEST_VOLUME_CAPACITY = 10L * 1024 * 1024 * 1024; // 10GB

  /** Remaining space for the 3 datanodes in {@link #SINGLE_RACK_THREE_DN_TOPOLOGY}. */
  private static final long[] SINGLE_RACK_THREE_DN_REMAINING = {
      3L * 1024 * 1024 * 1024, // dn0: 3GB remaining of 10GB
      5L * 1024 * 1024 * 1024, // dn1: 5GB remaining of 10GB
      7L * 1024 * 1024 * 1024  // dn2: 7GB remaining of 10GB
  };

  /** Low/high remaining capacity used to bias cross-rack weighted selection. */
  private static final long LOW_RACK_REMAINING = 90L * 1024 * 1024; // 90MB of 10GB
  private static final long HIGH_RACK_REMAINING = 9L * 1024 * 1024 * 1024; // 9GB of 10GB

  private static final long[] THREE_RACK_UNEQUAL_REMAINING = {
      5L * 1024 * 1024 * 1024, // RACK0: used/excluded rack, value irrelevant
      LOW_RACK_REMAINING,      // RACK1: low capacity
      HIGH_RACK_REMAINING      // RACK2: high capacity, expected to be preferred
  };

  /**
   * Capacity/remaining used only for the end-to-end re-replication test, which
   * writes a real key before patching capacities. Unlike the direct-call
   * weighted-selection scenarios above, every rack here must keep at least one
   * container slot so the initial write can still be placed, while the target
   * rack's aggregate remaining capacity is kept far larger to reliably bias the
   * single weighted-random replacement pick.
   */
  private static final long RE_REPLICATION_VOLUME_CAPACITY = 1024L * 1024 * 1024 * 1024; // 1TB
  private static final long RE_REPLICATION_LOW_REMAINING = 6L * 1024 * 1024 * 1024; // 6GB: 1 slot
  private static final long RE_REPLICATION_HIGH_REMAINING = 900L * 1024 * 1024 * 1024; // 900GB
  private static final long[] RE_REPLICATION_UNEQUAL_REMAINING = {
      RE_REPLICATION_LOW_REMAINING,  // RACK0: used/excluded rack, still needs a slot
      RE_REPLICATION_LOW_REMAINING,  // RACK1: low capacity, still has a slot
      RE_REPLICATION_HIGH_REMAINING  // RACK2: high capacity, expected to be preferred
  };

  /** Realistic soft reserve reported alongside remaining space in storage reports. */
  private static final long FREE_SPACE_TO_SPARE = 100L * 1024 * 1024; // 100MB

  /**
   * Remaining space for a datanode that is too full to accept a new container
   * (below the default 5GB container size) yet stays healthy and writable, well
   * above {@link #FREE_SPACE_TO_SPARE}, so safemode exit is not blocked.
   */
  private static final long NEARLY_FULL_REMAINING = 2L * 1024 * 1024 * 1024; // 2GB

  private static final int CAPACITY_SELECTION_ITERATIONS = 30;

  private enum ClusterReadyMode {
    PIPELINE,
    SAFE_MODE
  }

  private static final class Topology {
    private final String[] racks;
    private final String[] hosts;

    private Topology(String[] racks, String[] hosts) {
      this.racks = racks;
      this.hosts = hosts;
    }
  }

  private static void applyReplicationSpeedupConfig(OzoneConfiguration conf) {
    conf.setTimeDuration(ScmConfigKeys.OZONE_SCM_HEARTBEAT_PROCESS_INTERVAL,
        100, TimeUnit.MILLISECONDS);
    conf.setTimeDuration(ScmConfigKeys.OZONE_SCM_STALENODE_INTERVAL,
        3, TimeUnit.SECONDS);
    conf.setTimeDuration(ScmConfigKeys.OZONE_SCM_DEADNODE_INTERVAL,
        6, TimeUnit.SECONDS);
    conf.setTimeDuration("hdds.scm.replication.thread.interval",
        1, TimeUnit.SECONDS);
    conf.setTimeDuration("hdds.scm.replication.under.replicated.interval",
        5, TimeUnit.SECONDS);
    conf.setTimeDuration("hdds.scm.replication.over.replicated.interval",
        5, TimeUnit.SECONDS);
  }

  private static OzoneConfiguration configForPolicy(String placementClassName) {
    OzoneConfiguration conf = new OzoneConfiguration();
    conf.set(ScmConfigKeys.OZONE_SCM_CONTAINER_PLACEMENT_IMPL_KEY,
        placementClassName);
    applyReplicationSpeedupConfig(conf);
    return conf;
  }

  private static MiniOzoneCluster startCluster(OzoneConfiguration conf,
      String[] racks, String[] hosts, ClusterReadyMode readyMode) throws Exception {
    MiniOzoneCluster.Builder builder = MiniOzoneCluster.newBuilder(conf)
        .setNumDatanodes(racks != null ? racks.length : hosts.length);
    if (racks != null) {
      builder.setRacks(racks);
    }
    if (hosts != null) {
      builder.setHosts(hosts);
    }
    MiniOzoneCluster cluster = builder.build();
    cluster.waitForClusterToBeReady();
    if (readyMode == ClusterReadyMode.PIPELINE) {
      cluster.waitForPipelineTobeReady(ReplicationFactor.THREE, 60_000);
    } else {
      cluster.waitTobeOutOfSafeMode();
    }
    return cluster;
  }

  private static MiniOzoneCluster startCluster(OzoneConfiguration conf,
      Topology topology, ClusterReadyMode readyMode) throws Exception {
    return startCluster(conf, topology.racks, topology.hosts, readyMode);
  }

  @FunctionalInterface
  private interface ClusterTestBody {
    void run(MiniOzoneCluster cluster, StorageContainerManager scm) throws Exception;
  }

  private static void withCluster(String placementClassName, Topology topology,
      ClusterReadyMode readyMode, ClusterTestBody testBody) throws Exception {
    OzoneConfiguration conf = configForPolicy(placementClassName);
    try (MiniOzoneCluster cluster = startCluster(conf, topology, readyMode)) {
      testBody.run(cluster, cluster.getStorageContainerManager());
    }
  }

  static Stream<Arguments> rackAwarePolicies() {
    return Stream.of(
        Arguments.of(RACK_AWARE_POLICY),
        Arguments.of(RACK_SCATTER_POLICY));
  }

  static Stream<Arguments> crossRackCapacityScenarios() {
    return Stream.of(
        Arguments.of("twoRacksUnequal", TWO_RACK_TOPOLOGY,
            new long[] {LOW_RACK_REMAINING, HIGH_RACK_REMAINING}, RACK0, RACK1, 0.85),
        Arguments.of("threeRacksUnequal", THREE_RACK_TOPOLOGY,
            THREE_RACK_UNEQUAL_REMAINING, RACK0, RACK2, 0.85));
  }

  static Stream<Arguments> crossRackCapacityScenariosWithPolicies() {
    return rackAwarePolicies().flatMap(policyArgs -> crossRackCapacityScenarios()
        .map(scenarioArgs -> Arguments.of(
            policyArgs.get()[0],
            scenarioArgs.get()[0],
            scenarioArgs.get()[1],
            scenarioArgs.get()[2],
            scenarioArgs.get()[3],
            scenarioArgs.get()[4],
            scenarioArgs.get()[5])));
  }

  @ParameterizedTest(name = "{0}")
  @MethodSource("rackAwarePolicies")
  void testContainerPlacementWithPolicy(String placementClassName) throws Exception {
    withCluster(placementClassName, TWO_RACK_TOPOLOGY,
        ClusterReadyMode.PIPELINE, (cluster, scm) -> {
          assertEquals(placementClassName,
              scm.getContainerPlacementPolicy().getClass().getName(),
              "Placement policy was not set correctly");
          assertPipelinesSpanMultipleRacks(cluster);
          assertContainerReplicationIsRackAware(cluster, null);
        });

    assertTrue(new StaticMapping().getSwitchMap().isEmpty(),
        "Static mapping should be cleared after cluster shutdown");
  }

  /**
   * Verifies cross-rack replica selection prefers the rack with the highest
   * aggregate remaining capacity when two replicas already occupy a low-capacity rack.
   */
  @ParameterizedTest(name = "{0} / {1}")
  @MethodSource("crossRackCapacityScenariosWithPolicies")
  void testCrossRackPlacementPrefersHighCapacityRack(String placementClassName,
      String scenarioName, Topology topology, long[] remainingPerRack,
      String usedRack, String expectedRack, double minFractionOnExpectedRack)
      throws Exception {
    withCluster(placementClassName, topology, ClusterReadyMode.SAFE_MODE,
        (cluster, scm) -> {
          applyRackRemainingCapacities(scm, topology.racks, remainingPerRack,
              TEST_VOLUME_CAPACITY, FREE_SPACE_TO_SPARE);

          List<DatanodeDetails> usedNodes = getDatanodesOnRack(scm, usedRack, 2);
          assertEquals(2, usedNodes.size(),
              scenarioName + " requires two datanodes on " + usedRack);

          PlacementPolicy policy = scm.getContainerPlacementPolicy();
          int selectionsOnExpectedRack = 0;
          for (int i = 0; i < CAPACITY_SELECTION_ITERATIONS; i++) {
            List<DatanodeDetails> chosen = policy.chooseDatanodes(
                usedNodes, Collections.emptyList(), null, 1, 0, 0);
            assertEquals(1, chosen.size(), scenarioName);
            assertFalse(usedRack.equals(chosen.get(0).getNetworkLocation()),
                scenarioName + " cross-rack replica must not land on " + usedRack);
            if (expectedRack.equals(chosen.get(0).getNetworkLocation())) {
              selectionsOnExpectedRack++;
            }
          }

          assertTrue(
              selectionsOnExpectedRack >= CAPACITY_SELECTION_ITERATIONS
                  * minFractionOnExpectedRack,
              scenarioName + " expected cross-rack replica on " + expectedRack
                  + " but got " + selectionsOnExpectedRack + " of "
                  + CAPACITY_SELECTION_ITERATIONS);
        });
  }

  /**
   * A nearly-full datanode must not receive new containers, yet the cluster
   * should remain healthy enough for safemode exit.
   */
  @ParameterizedTest(name = "{0}")
  @MethodSource("rackAwarePolicies")
  void testNearlyFullDatanodeExcludedFromPlacement(String placementClassName)
      throws Exception {
    withCluster(placementClassName, THREE_RACK_TOPOLOGY,
        ClusterReadyMode.SAFE_MODE, (cluster, scm) -> {
          NodeManager nodeManager = scm.getScmNodeManager();
          applyRackRemainingCapacities(scm, THREE_RACK_TOPOLOGY.racks,
              new long[] {TEST_VOLUME_CAPACITY, TEST_VOLUME_CAPACITY, TEST_VOLUME_CAPACITY},
              TEST_VOLUME_CAPACITY, FREE_SPACE_TO_SPARE);

          DatanodeDetails nearlyFullDn = getDatanodesOnRack(scm, RACK0, 1).get(0);
          setDatanodeRemaining(scm, nearlyFullDn, TEST_VOLUME_CAPACITY,
              NEARLY_FULL_REMAINING, FREE_SPACE_TO_SPARE);

          DatanodeInfo nearlyFullInfo =
              (DatanodeInfo) nodeManager.getNode(nearlyFullDn.getID());
          assertFalse(nodeManager.hasAvailableSpace(nearlyFullInfo),
              "Nearly-full datanode should not have an available container slot");
          assertTrue(nearlyFullInfo.getNodeStatus().isNodeWritable(),
              "Nearly-full datanode should remain writable for safemode exit");

          PlacementPolicy policy = scm.getContainerPlacementPolicy();
          assertTrue(nodeManager.getAllNodes().stream()
                  .anyMatch(dn -> !RACK0.equals(dn.getNetworkLocation())),
              "Other racks must remain eligible while one datanode is nearly full");

          for (int i = 0; i < CAPACITY_SELECTION_ITERATIONS; i++) {
            List<DatanodeDetails> chosen = policy.chooseDatanodes(
                Collections.emptyList(), Collections.emptyList(), null, 3, 0, 0);
            assertEquals(3, chosen.size());
            assertTrue(chosen.stream()
                    .noneMatch(dn -> dn.getID().equals(nearlyFullDn.getID())),
                "Nearly-full datanode must not be selected");
            assertTrue(getRacks(chosen).size() >= 2,
                "Rack-aware layout must still be satisfied");
          }
        });
  }

  /**
   * End-to-end re-replication test with heterogeneous rack capacities. After
   * skewing rack weights, stopping a replica on a low-capacity rack should place
   * the replacement on the highest-capacity eligible rack.
   */
  @ParameterizedTest(name = "{0}")
  @MethodSource("rackAwarePolicies")
  void testReReplicationPrefersHighCapacityRack(String placementClassName)
      throws Exception {
    withCluster(placementClassName, THREE_RACK_TOPOLOGY,
        ClusterReadyMode.SAFE_MODE, (cluster, scm) -> {
          applyRackRemainingCapacities(scm, THREE_RACK_TOPOLOGY.racks,
              RE_REPLICATION_UNEQUAL_REMAINING,
              RE_REPLICATION_VOLUME_CAPACITY, FREE_SPACE_TO_SPARE);
          assertContainerReplicationIsRackAware(cluster, RACK2);
        });
  }

  /**
   * Regression test for a safemode hang caused by heterogeneous but healthy
   * per-datanode remaining capacity: a rack with 3 datanodes at 3GB, 5GB and
   * 7GB remaining out of 10GB capacity each must not block safemode exit or
   * placement across the rack.
   */
  @ParameterizedTest(name = "{0}")
  @MethodSource("rackAwarePolicies")
  void testSingleRackHeterogeneousCapacityExitsSafeMode(String placementClassName)
      throws Exception {
    withCluster(placementClassName, SINGLE_RACK_THREE_DN_TOPOLOGY,
        ClusterReadyMode.SAFE_MODE, (cluster, scm) -> {
          applyPerDatanodeRemainingCapacities(scm,
              SINGLE_RACK_THREE_DN_TOPOLOGY.hosts, SINGLE_RACK_THREE_DN_REMAINING,
              TEST_VOLUME_CAPACITY, FREE_SPACE_TO_SPARE);

          assertFalse(scm.isInSafeMode(),
              "SCM should not be stuck in safemode with heterogeneous "
                  + "per-datanode remaining capacity");

          // The datanode with only 3GB remaining has less usable space than the
          // default 5GB container size, so it must be skipped, but a datanode
          // with a free slot (5GB/7GB remaining) must still be selectable.
          PlacementPolicy policy = scm.getContainerPlacementPolicy();
          List<DatanodeDetails> chosen = policy.chooseDatanodes(
              Collections.emptyList(), Collections.emptyList(), null, 1, 0, 0);
          assertEquals(1, chosen.size(),
              "A datanode with a free container slot should remain selectable");
        });
  }

  @Nested
  @TestInstance(TestInstance.Lifecycle.PER_CLASS)
  class WithRacksAndHosts {

    private MiniOzoneCluster cluster;

    @BeforeAll
    void init() throws Exception {
      cluster = startCluster(configForPolicy(RACK_AWARE_POLICY),
          TWO_RACK_TOPOLOGY, ClusterReadyMode.PIPELINE);
    }

    @AfterAll
    void tearDown() {
      if (cluster != null) {
        cluster.shutdown();
      }
    }

    @Test
    void testDatanodesHaveCorrectRack() {
      assertRackAssignments(cluster, TWO_RACK_TOPOLOGY.racks);
    }

    @Test
    void testDatanodesHaveCorrectHostname() {
      assertHostnameAssignments(cluster, TWO_RACK_TOPOLOGY.hosts);
    }

    @Test
    void testRatisPipelineSpansMultipleRacks() {
      assertPipelinesSpanMultipleRacks(cluster);
    }

    @Test
    void testContainerReplicationIsRackAware() throws Exception {
      assertContainerReplicationIsRackAware(cluster, null);
    }
  }

  @Nested
  @TestInstance(TestInstance.Lifecycle.PER_CLASS)
  class WithRacksOnly {

    private MiniOzoneCluster cluster;

    @BeforeAll
    void init() throws Exception {
      OzoneConfiguration conf = new OzoneConfiguration();
      applyReplicationSpeedupConfig(conf);
      cluster = startCluster(conf, TWO_RACK_TOPOLOGY.racks, null,
          ClusterReadyMode.PIPELINE);
    }

    @AfterAll
    void tearDown() {
      if (cluster != null) {
        cluster.shutdown();
      }
    }

    @Test
    void testDatanodesHaveCorrectRack() {
      assertRackAssignments(cluster, TWO_RACK_TOPOLOGY.racks);
    }

    @Test
    void testRatisPipelineSpansMultipleRacks() {
      assertPipelinesSpanMultipleRacks(cluster);
    }

    @Test
    void testContainerReplicationIsRackAware() throws Exception {
      assertContainerReplicationIsRackAware(cluster, null);
    }
  }

  @Nested
  @TestInstance(TestInstance.Lifecycle.PER_CLASS)
  class WithHostsOnly {

    private MiniOzoneCluster cluster;

    @BeforeAll
    void init() throws Exception {
      OzoneConfiguration conf = new OzoneConfiguration();
      applyReplicationSpeedupConfig(conf);
      cluster = startCluster(conf, null,
          TWO_RACK_TOPOLOGY.hosts, ClusterReadyMode.PIPELINE);
    }

    @AfterAll
    void tearDown() {
      if (cluster != null) {
        cluster.shutdown();
      }
    }

    @Test
    void testDatanodesHaveCorrectHostname() {
      assertHostnameAssignments(cluster, TWO_RACK_TOPOLOGY.hosts);
    }

    @Test
    void testDatanodesAllInDefaultRack() {
      NodeManager nodeManager =
          cluster.getStorageContainerManager().getScmNodeManager();
      for (DatanodeDetails dn : nodeManager.getAllNodes()) {
        assertEquals(NetworkTopology.DEFAULT_RACK, dn.getNetworkLocation(),
            "Datanode " + dn.getHostName()
                + " should be in default rack when no racks are configured");
      }
    }
  }

  private static void assertContainerReplicationIsRackAware(
      MiniOzoneCluster cluster, String preferredReplacementRack)
      throws Exception {
    StorageContainerManager scm = cluster.getStorageContainerManager();
    writeTestKey(cluster);

    ContainerSelection selection = findContainerForReplicationTest(scm,
        preferredReplacementRack);
    assertNotNull(selection.container,
        "Should find a container with 3 replicas");
    final DatanodeDetails deadDn = selection.datanodeToStop;

    cluster.shutdownHddsDatanode(deadDn);
    waitForDatanodeDead(scm, deadDn);
    waitForRackAwareReplication(scm, selection.container.containerID(), deadDn,
        preferredReplacementRack);

    Set<ContainerReplica> finalReplicas = scm.getContainerManager()
        .getContainerReplicas(selection.container.containerID());
    Set<String> racks = getReplicaRacks(finalReplicas);

    assertTrue(racks.size() >= 2,
        "Container replicas after re-replication should span at least "
            + "2 racks, but were on: " + racks);
    if (preferredReplacementRack != null) {
      assertTrue(finalReplicas.stream()
              .anyMatch(replica -> preferredReplacementRack.equals(
                  replica.getDatanodeDetails().getNetworkLocation())),
          "Replacement replica should land on high-capacity rack "
              + preferredReplacementRack + ", but replicas were on " + racks);
    }
  }

  private static void writeTestKey(MiniOzoneCluster cluster) throws Exception {
    try (OzoneClient client = cluster.newClient()) {
      ObjectStore store = client.getObjectStore();
      store.createVolume("testvol");
      OzoneVolume volume = store.getVolume("testvol");
      volume.createBucket("testbucket");
      OzoneBucket bucket = volume.getBucket("testbucket");

      byte[] data = "test-data".getBytes(StandardCharsets.UTF_8);
      try (OzoneOutputStream out = bucket.createKey(
          "testkey", data.length,
          RatisReplicationConfig.getInstance(ReplicationFactor.THREE),
          new HashMap<>())) {
        out.write(data);
      }
    }
  }

  private static final class ContainerSelection {
    private final ContainerInfo container;
    private final DatanodeDetails datanodeToStop;

    private ContainerSelection(ContainerInfo container,
        DatanodeDetails datanodeToStop) {
      this.container = container;
      this.datanodeToStop = datanodeToStop;
    }
  }

  private static ContainerSelection findContainerForReplicationTest(
      StorageContainerManager scm, String preferredReplacementRack) throws Exception {
    for (ContainerInfo container : scm.getContainerManager().getContainers()) {
      Set<ContainerReplica> replicas =
          scm.getContainerManager().getContainerReplicas(container.containerID());
      if (replicas.size() != 3) {
        continue;
      }
      DatanodeDetails datanodeToStop;
      if (preferredReplacementRack != null) {
        datanodeToStop = replicas.stream()
            .map(ContainerReplica::getDatanodeDetails)
            .filter(dn -> !preferredReplacementRack.equals(dn.getNetworkLocation()))
            .findFirst()
            .orElse(null);
        if (datanodeToStop == null) {
          continue;
        }
      } else {
        datanodeToStop = replicas.iterator().next().getDatanodeDetails();
      }
      return new ContainerSelection(container, datanodeToStop);
    }
    return new ContainerSelection(null, null);
  }

  private static void waitForDatanodeDead(StorageContainerManager scm,
      DatanodeDetails datanode) throws TimeoutException, InterruptedException {
    GenericTestUtils.waitFor(() -> {
      try {
        return scm.getScmNodeManager()
            .getNodeStatus(datanode)
            .getHealth() == HddsProtos.NodeState.DEAD;
      } catch (Exception e) {
        return false;
      }
    }, 500, 30_000);
  }

  private static void waitForRackAwareReplication(
      StorageContainerManager scm, ContainerID containerID,
      DatanodeDetails stoppedDn, String preferredReplacementRack)
      throws TimeoutException, InterruptedException {
    GenericTestUtils.waitFor(() -> {
      try {
        Set<ContainerReplica> current = scm.getContainerManager()
            .getContainerReplicas(containerID);

        boolean deadReplicaRemoved = current.stream()
            .noneMatch(replica -> stoppedDn.equals(
                replica.getDatanodeDetails()));
        boolean replicaCountRestored = current.size() >= 3;
        boolean rackAware = getReplicaRacks(current).size() >= 2;
        boolean onPreferredRack = preferredReplacementRack == null
            || current.stream().anyMatch(replica ->
            preferredReplacementRack.equals(
                replica.getDatanodeDetails().getNetworkLocation()));

        return deadReplicaRemoved && replicaCountRestored && rackAware && onPreferredRack;
      } catch (Exception e) {
        return false;
      }
    }, 1_000, 60_000);
  }

  private static Set<String> getRacks(Collection<DatanodeDetails> datanodes) {
    return datanodes.stream()
        .map(DatanodeDetails::getNetworkLocation)
        .collect(Collectors.toSet());
  }

  private static Set<String> getReplicaRacks(Set<ContainerReplica> replicas) {
    return replicas.stream()
        .map(replica -> replica.getDatanodeDetails().getNetworkLocation())
        .collect(Collectors.toSet());
  }

  private static List<String> uniqueRacksInOrder(String[] rackLayout) {
    return new ArrayList<>(new LinkedHashSet<>(Arrays.asList(rackLayout)));
  }

  private static void applyRackRemainingCapacities(StorageContainerManager scm,
      String[] rackLayout, long[] remainingPerRack, long capacity,
      long freeSpaceToSpare) {
    List<String> racks = uniqueRacksInOrder(rackLayout);
    assertEquals(remainingPerRack.length, racks.size(),
        "remainingPerRack length must match distinct rack count");
    Map<String, Long> remainingByRack = new HashMap<>();
    for (int i = 0; i < racks.size(); i++) {
      remainingByRack.put(racks.get(i), remainingPerRack[i]);
    }
    for (DatanodeDetails dn : scm.getScmNodeManager().getAllNodes()) {
      setDatanodeRemaining(scm, dn, capacity,
          remainingByRack.get(dn.getNetworkLocation()), freeSpaceToSpare);
    }
  }

  private static void applyPerDatanodeRemainingCapacities(
      StorageContainerManager scm, String[] hostsInOrder, long[] remainingPerDn,
      long capacity, long freeSpaceToSpare) {
    assertEquals(hostsInOrder.length, remainingPerDn.length,
        "remainingPerDn length must match datanode count");
    Map<String, DatanodeDetails> byHost = scm.getScmNodeManager().getAllNodes().stream()
        .collect(Collectors.toMap(DatanodeDetails::getHostName, dn -> dn));
    for (int i = 0; i < hostsInOrder.length; i++) {
      DatanodeDetails dn = byHost.get(hostsInOrder[i]);
      assertNotNull(dn, "Datanode not found for host " + hostsInOrder[i]);
      setDatanodeRemaining(scm, dn, capacity, remainingPerDn[i], freeSpaceToSpare);
    }
  }

  private static void setDatanodeRemaining(StorageContainerManager scm,
      DatanodeDetails dn, long capacity, long remaining, long freeSpaceToSpare) {
    DatanodeInfo info = (DatanodeInfo) scm.getScmNodeManager().getNode(dn.getID());
    long used = Math.max(0L, capacity - remaining);
    StorageReportProto storageReport = HddsTestUtils.createStorageReport(
        dn.getID(), "/data-" + dn.getHostName(), capacity, used, remaining, null)
        .toBuilder()
        .setFreeSpaceToSpare(freeSpaceToSpare)
        .build();
    MetadataStorageReportProto metadataReport =
        HddsTestUtils.createMetadataStorageReport(
            "/metadata-" + dn.getHostName(), capacity, 0, remaining, null);
    info.updateStorageReports(Collections.singletonList(storageReport));
    info.updateMetaDataStorageReports(Collections.singletonList(metadataReport));
  }

  private static List<DatanodeDetails> getDatanodesOnRack(
      StorageContainerManager scm, String rack, int limit) {
    return scm.getScmNodeManager().getAllNodes().stream()
        .filter(dn -> rack.equals(dn.getNetworkLocation()))
        .limit(limit)
        .collect(Collectors.toList());
  }

  private void assertRackAssignments(MiniOzoneCluster cluster,
                                     String[] expectedRacks) {
    NodeManager nodeManager =
        cluster.getStorageContainerManager().getScmNodeManager();
    List<? extends DatanodeDetails> allNodes = nodeManager.getAllNodes();

    assertEquals(expectedRacks.length, allNodes.size(),
        "Number of registered datanodes should match number of configured racks");

    long actualRack0 = allNodes.stream()
        .filter(dn -> RACK0.equals(dn.getNetworkLocation()))
        .count();
    long actualRack1 = allNodes.stream()
        .filter(dn -> RACK1.equals(dn.getNetworkLocation()))
        .count();

    long expectedRack0 =
        Arrays.stream(expectedRacks).filter(RACK0::equals).count();
    long expectedRack1 =
        Arrays.stream(expectedRacks).filter(RACK1::equals).count();

    assertEquals(expectedRack0, actualRack0,
        "Expected " + expectedRack0 + " datanodes on " + RACK0);
    assertEquals(expectedRack1, actualRack1,
        "Expected " + expectedRack1 + " datanodes on " + RACK1);

    for (DatanodeDetails dn : allNodes) {
      String location = dn.getNetworkLocation();
      assertNotNull(location,
          "Network location must not be null for " + dn.getHostName());
      assertTrue(location.equals(RACK0) || location.equals(RACK1),
          "Unexpected rack for datanode " + dn.getHostName()
              + ": " + location);
    }
  }

  private void assertHostnameAssignments(MiniOzoneCluster cluster,
                                         String[] expectedHosts) {
    NodeManager nodeManager =
        cluster.getStorageContainerManager().getScmNodeManager();
    List<? extends DatanodeDetails> allNodes = nodeManager.getAllNodes();

    assertEquals(expectedHosts.length, allNodes.size(),
        "Number of registered datanodes should match number of configured hosts");

    Set<String> actual = allNodes.stream()
        .map(DatanodeDetails::getHostName)
        .collect(Collectors.toSet());

    Set<String> expected = Arrays.stream(expectedHosts)
        .collect(Collectors.toSet());

    assertEquals(expected, actual,
        "Registered datanode hostnames should match configured hosts");
  }

  private static void assertPipelinesSpanMultipleRacks(MiniOzoneCluster cluster) {
    List<Pipeline> pipelines = cluster.getStorageContainerManager()
        .getPipelineManager()
        .getPipelines(
            RatisReplicationConfig.getInstance(ReplicationFactor.THREE),
            Pipeline.PipelineState.OPEN);

    assertFalse(pipelines.isEmpty(),
        "There should be at least one open RATIS THREE pipeline");

    for (Pipeline pipeline : pipelines) {
      Set<String> racks = getRacks(pipeline.getNodes());
      assertTrue(racks.size() >= 2,
          "Pipeline " + pipeline.getId()
              + " should span at least 2 racks, but spans: " + racks);
    }
  }
}
