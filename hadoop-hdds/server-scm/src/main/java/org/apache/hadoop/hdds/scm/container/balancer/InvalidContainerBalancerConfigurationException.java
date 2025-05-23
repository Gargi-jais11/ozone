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

package org.apache.hadoop.hdds.scm.container.balancer;

import java.io.IOException;
import org.apache.hadoop.hdds.scm.ha.SCMServiceException;

/**
 * Signals that {@link ContainerBalancerConfiguration} contains invalid
 * configuration value(s).
 */
public class InvalidContainerBalancerConfigurationException extends
    SCMServiceException {

  /**
   * Constructs an InvalidContainerBalancerConfigurationException with no detail
   * message. A detail message is a String that describes this particular
   * exception.
   */
  public InvalidContainerBalancerConfigurationException() {
    super();
  }

  /**
   * Constructs an InvalidContainerBalancerConfigurationException with the
   * specified detail message. A detail message is a String that describes
   * this particular exception.
   *
   * @param s the String that contains a detailed message
   */
  public InvalidContainerBalancerConfigurationException(String s) {
    super(s);
  }

  public InvalidContainerBalancerConfigurationException(String s,
                                                        IOException e) {
    super(s, e);
  }
}
