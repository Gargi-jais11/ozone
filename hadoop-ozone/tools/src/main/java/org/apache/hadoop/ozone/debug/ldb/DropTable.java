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

package org.apache.hadoop.ozone.debug.ldb;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Callable;
import org.apache.hadoop.hdds.utils.db.managed.ManagedRocksDB;
import org.apache.hadoop.ozone.debug.RocksDBUtils;
import org.rocksdb.ColumnFamilyDescriptor;
import org.rocksdb.ColumnFamilyHandle;
import picocli.CommandLine;

/**
 * Drop a column Family/Table in db.
 */
@CommandLine.Command(
        name = "drop_column_family",
        description = "drop column family in db."
)
public class DropTable implements Callable<Void> {

  @CommandLine.Option(names = {"--column-family", "--column_family"},
      description = "Table name")
  private String tableName;

  @CommandLine.ParentCommand
  private RDBParser parent;

  @Override
  public Void call() throws Exception {
    List<ColumnFamilyDescriptor> cfs =
        RocksDBUtils.getColumnFamilyDescriptors(parent.getDbPath());
    final List<ColumnFamilyHandle> columnFamilyHandleList =
        new ArrayList<>();
    try (ManagedRocksDB rocksDB = ManagedRocksDB.open(
        parent.getDbPath(), cfs, columnFamilyHandleList)) {
      byte[] nameBytes = tableName.getBytes(StandardCharsets.UTF_8);
      ColumnFamilyHandle toBeDeletedCf = null;
      for (ColumnFamilyHandle cf : columnFamilyHandleList) {
        if (Arrays.equals(cf.getName(), nameBytes)) {
          toBeDeletedCf = cf;
          break;
        }
      }
      if (toBeDeletedCf == null) {
        System.err.println(tableName + " is not in a column family in DB "
            + parent.getDbPath());
      } else {
        System.out.println(tableName + " will be deleted from DB "
            + parent.getDbPath());
        rocksDB.get().dropColumnFamily(toBeDeletedCf);
      }
    }
    return null;
  }
}
