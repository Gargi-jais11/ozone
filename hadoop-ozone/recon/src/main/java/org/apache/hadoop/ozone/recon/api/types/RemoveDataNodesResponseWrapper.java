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

package org.apache.hadoop.ozone.recon.api.types;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.HashMap;
import java.util.Map;
import javax.xml.bind.annotation.XmlElement;

/**
 * Class that represents the API Response structure of Datanodes.
 */
public class RemoveDataNodesResponseWrapper {

  @XmlElement(name = "datanodesResponseMap")
  @JsonInclude(JsonInclude.Include.NON_NULL)
  private Map<String, DatanodesResponse> datanodesResponseMap = new HashMap<>();

  public Map<String, DatanodesResponse> getDatanodesResponseMap() {
    return datanodesResponseMap;
  }

  public void setDatanodesResponseMap(
      Map<String, DatanodesResponse> datanodesResponseMap) {
    this.datanodesResponseMap = datanodesResponseMap;
  }
}
