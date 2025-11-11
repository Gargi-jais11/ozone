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

package org.apache.hadoop.hdds.server;

import java.io.File;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Collection;
import org.apache.hadoop.fs.permission.FsPermission;
import org.apache.hadoop.hdds.HddsConfigKeys;
import org.apache.hadoop.hdds.conf.ConfigurationSource;
import org.apache.hadoop.hdds.conf.OzoneConfiguration;
import org.apache.hadoop.hdds.recon.ReconConfigKeys;
import org.apache.hadoop.hdds.scm.ScmConfigKeys;
import org.apache.hadoop.ipc.RPC;
import org.apache.hadoop.ipc.Server;
import org.apache.hadoop.ozone.OzoneConfigKeys;
import org.apache.hadoop.ozone.common.Storage;
import org.apache.hadoop.security.UserGroupInformation;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Generic utilities for all HDDS/Ozone servers.
 */
public final class ServerUtils {

  private static final Logger LOG = LoggerFactory.getLogger(
      ServerUtils.class);

  private ServerUtils() {
  }

  /**
   * Checks that a given value is with a range.
   *
   * For example, sanitizeUserArgs(17, 3, 5, 10)
   * ensures that 17 is greater/equal than 3 * 5 and less/equal to 3 * 10.
   *
   * @param key           - config key of the value
   * @param valueTocheck  - value to check
   * @param baseKey       - config key of the baseValue
   * @param baseValue     - the base value that is being used.
   * @param minFactor     - range min - a 2 here makes us ensure that value
   *                        valueTocheck is at least twice the baseValue.
   * @param maxFactor     - range max
   * @return long
   */
  public static long sanitizeUserArgs(String key, long valueTocheck,
      String baseKey, long baseValue, long minFactor, long maxFactor) {
    long minLimit = baseValue * minFactor;
    long maxLimit = baseValue * maxFactor;
    if (valueTocheck < minLimit) {
      LOG.warn(
          "{} value = {} is smaller than min = {} based on"
          + " the key value of {}, reset to the min value {}.",
          key, valueTocheck, minLimit, baseKey, minLimit);
      valueTocheck = minLimit;
    } else if (valueTocheck > maxLimit) {
      LOG.warn(
          "{} value = {} is larger than max = {} based on"
          + " the key value of {}, reset to the max value {}.",
          key, valueTocheck, maxLimit, baseKey, maxLimit);
      valueTocheck = maxLimit;
    }

    return valueTocheck;
  }

  /**
   * After starting an RPC server, updates configuration with the actual
   * listening address of that server. The listening address may be different
   * from the configured address if, for example, the configured address uses
   * port 0 to request use of an ephemeral port.
   *
   * @param conf configuration to update
   * @param rpcAddressKey configuration key for RPC server address
   * @param addr configured address
   * @param rpcServer started RPC server.
   */
  public static InetSocketAddress updateRPCListenAddress(
      OzoneConfiguration conf, String rpcAddressKey,
      InetSocketAddress addr, RPC.Server rpcServer) {
    return updateListenAddress(conf, rpcAddressKey, addr,
        rpcServer.getListenerAddress());
  }

  /**
   * After starting an server, updates configuration with the actual
   * listening address of that server. The listening address may be different
   * from the configured address if, for example, the configured address uses
   * port 0 to request use of an ephemeral port.
   *
   * @param conf       configuration to update
   * @param addressKey configuration key for RPC server address
   * @param addr       configured address
   * @param listenAddr the real listening address.
   */
  public static InetSocketAddress updateListenAddress(OzoneConfiguration conf,
      String addressKey, InetSocketAddress addr, InetSocketAddress listenAddr) {
    InetSocketAddress updatedAddr = new InetSocketAddress(addr.getHostString(),
        listenAddr.getPort());
    conf.set(addressKey,
        addr.getHostString() + ":" + listenAddr.getPort());
    return updatedAddr;
  }

  /**
   * Get the location where SCM should store its metadata directories.
   * Fall back to OZONE_METADATA_DIRS/scm if not defined.
   *
   * @param conf
   * @return File object pointing to the SCM database directory
   */
  public static File getScmDbDir(ConfigurationSource conf) {
    File metadataDir = getDirectoryFromConfig(conf,
        ScmConfigKeys.OZONE_SCM_DB_DIRS, "SCM");
    if (metadataDir != null) {
      return metadataDir;
    }

    LOG.warn("{} is not configured. We recommend adding this setting. " +
        "Falling back to {} instead.",
        ScmConfigKeys.OZONE_SCM_DB_DIRS, HddsConfigKeys.OZONE_METADATA_DIRS);
    return getOzoneMetaDirPath(conf, "scm");
  }

  /**
   * Utility method to retrieve the value of a key representing a DB directory
   * and create a File object for the directory. The method also sets the
   * directory permissions based on the configuration.
   *
   * @param conf configuration bag
   * @param key Key to test
   * @param componentName Which component's key is this
   * @return File created from the value of the key in conf.
   */
  public static File getDirectoryFromConfig(ConfigurationSource conf,
                                            String key,
                                            String componentName) {
    final Collection<String> metadirs = conf.getTrimmedStringCollection(key);
    if (metadirs.size() > 1) {
      throw new IllegalArgumentException(
          "Bad config setting " + key +
              ". " + componentName +
              " does not support multiple metadata dirs currently");
    }

    if (metadirs.size() == 1) {
      final File dbDirPath = new File(metadirs.iterator().next());
      if (!dbDirPath.mkdirs() && !dbDirPath.exists()) {
        throw new IllegalArgumentException("Unable to create directory " +
            dbDirPath + " specified in configuration setting " +
            key);
      }
      try {
        Path path = dbDirPath.toPath();
        // Fetch the permissions for the respective component from the config
        String permissionValue = getPermissions(key, conf);
        String symbolicPermission = getSymbolicPermission(permissionValue);

        // Set the permissions for the directory
        Files.setPosixFilePermissions(path,
            PosixFilePermissions.fromString(symbolicPermission));
      } catch (Exception e) {
        throw new RuntimeException("Failed to set directory permissions for " +
            dbDirPath + ": " + e.getMessage(), e);
      }
      return dbDirPath;
    }

    return null;
  }

  /**
   * Fetches the symbolic representation of the permission value.
   *
   * @param permissionValue the permission value (octal or symbolic)
   * @return the symbolic representation of the permission value
   */
  private static String getSymbolicPermission(String permissionValue) {
    if (isSymbolic(permissionValue)) {
      // For symbolic representation, use it directly
      return permissionValue;
    } else {
      // For octal representation, convert it to FsPermission object and then
      // to symbolic representation
      short octalPermission = Short.parseShort(permissionValue, 8);
      FsPermission fsPermission = new FsPermission(octalPermission);
      return fsPermission.toString();
    }
  }

  /**
   * Checks if the permission value is in symbolic representation.
   *
   * @param permissionValue the permission value to check
   * @return true if the permission value is in symbolic representation,
   * false otherwise
   */
  private static boolean isSymbolic(String permissionValue) {
    return permissionValue.matches(".*[rwx].*");
  }

  /**
   * Retrieves the permissions' configuration value for a given config key.
   *
   * @param key  The configuration key.
   * @param conf The ConfigurationSource object containing the config
   * @return The permissions' configuration value for the specified key.
   * @throws IllegalArgumentException If the configuration value is not defined
   */
  public static String getPermissions(String key, ConfigurationSource conf) {
    String configName = "";

    // Assign the appropriate config name based on the KEY
    if (key.equals(ReconConfigKeys.OZONE_RECON_DB_DIR)) {
      configName = ReconConfigKeys.OZONE_RECON_DB_DIRS_PERMISSIONS;
    } else if (key.equals(ScmConfigKeys.OZONE_SCM_DB_DIRS)) {
      configName = ScmConfigKeys.OZONE_SCM_DB_DIRS_PERMISSIONS;
    } else if (key.equals(OzoneConfigKeys.OZONE_OM_DB_DIRS)) {
      configName = OzoneConfigKeys.OZONE_OM_DB_DIRS_PERMISSIONS;
    } else {
      // If the permissions are not defined for the config, we make it fall
      // back to the default permissions for metadata files and directories
      configName = OzoneConfigKeys.OZONE_METADATA_DIRS_PERMISSIONS;
    }

    String configValue = conf.get(configName);
    if (configValue != null) {
      return configValue;
    }

    throw new IllegalArgumentException(
        "Invalid configuration value for key: " + key);
  }

  /**
   * Checks and creates Ozone Metadir Path if it does not exist.
   *
   * @param conf - Configuration
   * @return File MetaDir
   * @throws IllegalArgumentException if the configuration setting is not set
   */
  public static File getOzoneMetaDirPath(ConfigurationSource conf) {
    File dirPath = getDirectoryFromConfig(conf,
        HddsConfigKeys.OZONE_METADATA_DIRS, "Ozone");
    if (dirPath == null) {
      throw new IllegalArgumentException(
          HddsConfigKeys.OZONE_METADATA_DIRS + " must be defined.");
    }
    return dirPath;
  }

  /**
   * Returns the metadata directory path for a specific component with component-specific subdirectory.
   *
   * <p>This method creates isolated subdirectories for each Ozone component (SCM, OM, Datanode) under
   * the base {@code ozone.metadata.dirs} path. This isolation prevents file lock conflicts when
   * multiple components are colocated on the same host and share the same base metadata directory.
   *
   * <p><b>Directory Structure:</b>
   * <ul>
   *   <li>New installations: {@code {ozone.metadata.dirs}/{component}/}
   *       (e.g., {@code /data/metadata/scm/})</li>
   *   <li>Upgraded installations: {@code {ozone.metadata.dirs}/}
   *       (e.g., {@code /data/metadata/} - for backward compatibility)</li>
   * </ul>
   *
   * <p><b>Backward Compatibility:</b>
   * Prior to this change, all components stored their data directly in the base metadata directory,
   * which caused {@code OverlappingFileLockException} when multiple components were colocated.
   * To support seamless upgrades from older Ozone versions (like 2.0.0), this method detects if
   * component data already exists in the old location and continues using it. This avoids requiring
   * manual data migration during upgrades.
   *
   * <p><b>Examples:</b>
   * <pre>
   * // Scenario 1: Upgrade from Ozone 2.0.0 with SCM data at /data/metadata/scm.db
   * getOzoneMetaDirPath(conf, "scm") → /data/metadata/  (backward compatible)
   *
   * // Scenario 2: Fresh installation with no existing data
   * getOzoneMetaDirPath(conf, "scm") → /data/metadata/scm/  (new structure)
   *
   * // Scenario 3: Already migrated with data at /data/metadata/scm/scm.db
   * getOzoneMetaDirPath(conf, "scm") → /data/metadata/scm/  (new structure)
   * </pre>
   *
   * @param conf Configuration source
   * @param component Component name (e.g., "scm", "om", "datanode")
   * @return Directory path for the component's metadata
   * @throws IllegalArgumentException if unable to create the component directory
   * @see #hasComponentData(File, String)
   */
  public static File getOzoneMetaDirPath(ConfigurationSource conf, String component) {
    // Get the base metadata directory from ozone.metadata.dirs configuration
    File baseDir = getOzoneMetaDirPath(conf);

    // Construct the new component-specific subdirectory path
    // e.g., /data/metadata/scm/ for SCM component
    File componentDir = new File(baseDir, component);

    // STEP 1: Check if component data exists in the NEW location (component subdirectory)
    if (hasComponentData(componentDir, component)) {
      return componentDir;
    }

    // STEP 2: Check if component data exists in the OLD location (base directory)
    // This is the BACKWARD COMPATIBILITY path for upgrades from older Ozone versions.
    // In older versions (e.g., 2.0.0), all components stored data directly in the base
    // directory without component-specific subdirectories. For example:
    // - SCM stored data at /data/metadata/scm.db
    // - OM stored data at /data/metadata/om.db
    //
    // If we find existing data in the old location, we continue using the base directory
    // to avoid breaking existing installations and requiring manual data migration.
    if (hasComponentData(baseDir, component)) {
      return baseDir;
    }

    // STEP 3: No existing data found - this is a NEW installation
    // Create the component-specific subdirectory for isolation.
    if (!componentDir.mkdirs() && !componentDir.exists()) {
      throw new IllegalArgumentException("Unable to create directory " +
          componentDir + " under " + HddsConfigKeys.OZONE_METADATA_DIRS);
    }

    return componentDir;
  }

  /**
   * Checks if the given directory contains metadata for the specified component.
   *
   * <p>This method identifies component-specific marker files or directories that indicate
   * the presence of component data. It's used to detect where existing data is located
   * (either in the old flat structure or the new component-specific structure).
   *
   * <p><b>Component Markers:</b>
   * <ul>
   *   <li>SCM: {@code scm.db/} directory (RocksDB database)</li>
   *   <li>OM: {@code om.db/} directory (RocksDB database)</li>
   *   <li>Datanode: {@code datanode.id} file or {@code current/hdds/} directory</li>
   *   <li>scm-ha: {@code current/} directory (Ratis storage structure)</li>
   *   <li>Others: {@code VERSION} file (generic fallback)</li>
   * </ul>
   *
   * @param dir Directory to check for component data
   * @param component Component name
   * @return {@code true} if the directory contains data for this component, {@code false} otherwise
   */
  private static boolean hasComponentData(File dir, String component) {
    if (!dir.exists()) {
      return false;
    }

    switch (component) {
    case "scm":
      // SCM stores its metadata in a RocksDB database directory named scm.db
      return new File(dir, "scm.db").exists();

    case "om":
      // OM stores its metadata in a RocksDB database directory named om.db
      return new File(dir, "om.db").exists();

    case "datanode":
      // Datanode can use either:
      // - datanode.id file (primary marker)
      // - current/hdds/ directory (alternative marker for older versions)
      return new File(dir, "datanode.id").exists() ||
          new File(new File(dir, "current"), "hdds").exists();

    default:
      // For unknown/future components, fall back to checking for a VERSION file
      // which is typically created when a component initializes its storage
      return new File(dir, Storage.STORAGE_FILE_VERSION).exists();
    }
  }

  public static void setOzoneMetaDirPath(OzoneConfiguration conf,
                                         String path) {
    conf.set(HddsConfigKeys.OZONE_METADATA_DIRS, path);
  }

  /**
   * Returns with the service specific metadata directory.
   * <p>
   * If the directory is missing the method tries to create it.
   * Falls back to {ozone.metadata.dirs}/om if the key is not configured.
   *
   * @param conf The ozone configuration object
   * @param key  The configuration key which specify the directory.
   * @return The path of the directory.
   */
  public static File getDBPath(ConfigurationSource conf, String key) {
    final File dbDirPath =
        getDirectoryFromConfig(conf, key, "OM");
    if (dbDirPath != null) {
      return dbDirPath;
    }

    LOG.warn("{} is not configured. We recommend adding this setting. "
            + "Falling back to {} instead.", key,
        HddsConfigKeys.OZONE_METADATA_DIRS);
    return ServerUtils.getOzoneMetaDirPath(conf, "om");
  }

  public static String getRemoteUserName() {
    UserGroupInformation remoteUser = Server.getRemoteUser();
    return remoteUser != null ? remoteUser.getUserName() : null;
  }

  /**
   * Returns the default Ratis storage directory as {ozone.metadata.dirs}/{component}/ratis.
   * Used as fallback when component-specific Ratis directory is not configured.
   *
   * @param conf the configuration source
   * @param component the component name (e.g., "scm", "om", "datanode")
   * @return the path to the component-specific Ratis storage directory
   */
  public static String getDefaultRatisDirectory(ConfigurationSource conf, String component) {
    LOG.warn("Storage directory for Ratis is not configured for {}. It is a good " +
            "idea to map this to an SSD disk. Falling back to {} with component prefix",
        component, HddsConfigKeys.OZONE_METADATA_DIRS);
    File metaDirPath = getOzoneMetaDirPath(conf, component);
    return new File(metaDirPath, "ratis").getPath();
  }
}
