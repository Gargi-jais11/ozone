# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Alert diagnostic plugins: one module per Prometheus alertname.

Importing this package registers every built-in plugin (see registry.py) --
new alert types are added by dropping in a new module and decorating its
plugin class with @register_plugin, not by editing a central switch statement.
"""

from app.plugins import container_health  # noqa: F401  (registers itself)
from app.plugins import datanode_deletion_not_progressing  # noqa: F401  (registers itself)
from app.plugins import om_deletion_not_progressing  # noqa: F401  (registers itself)
from app.plugins import scm_deletion_not_progressing  # noqa: F401  (registers itself)
from app.plugins.registry import get_plugin, list_plugins, register_plugin

__all__ = ["get_plugin", "list_plugins", "register_plugin"]
