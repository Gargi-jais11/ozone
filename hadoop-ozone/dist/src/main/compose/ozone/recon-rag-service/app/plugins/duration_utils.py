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
"""Shared helper for plugins that propose halving an Ozone duration config.

Several remediation plans (datanode block-deleting interval, SCM
under-replicated queue interval, ...) follow the same shape: read the
current Ozone duration-typed property, halve it so the affected service
runs more often. Kept as one helper so plugins don't each re-implement the
same regex.
"""

import re

_DURATION_RE = re.compile(r"(\d+)([smhd])")


def halve_duration(value: str) -> str:
    """Halve an Ozone duration string like ``30s`` or ``2m``, preserving unit.

    Returns ``value`` unchanged if it doesn't match Ozone's simple
    ``<amount><unit>`` duration shape (``s``/``m``/``h``/``d``).
    """

    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        return value
    amount = int(match.group(1))
    unit = match.group(2)
    halved = max(1, amount // 2)
    return f"{halved}{unit}"
