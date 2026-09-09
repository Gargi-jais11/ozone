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
"""FastAPI application entry point for the recon-rag-service.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8642
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

# Configure logging before importing any module that grabs a module-level
# logger, so every subsequent log call (e.g. app.rag.llm_client.get_llm_client
# logging which LLMClient it selected, on the first /diagnose call) is emitted
# at the intended level instead of Python's unconfigured default.
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from app.api.routes import router  # noqa: E402  (must follow logging.basicConfig)

# Importing app.plugins triggers the @register_plugin decorators, populating
# the plugin registry before any request arrives.
import app.plugins  # noqa: F401,E402  (import for side effect, must follow other imports)

logging.getLogger(__name__).info(
    "recon-rag-service starting up; log_level=%s", settings.log_level
)

app = FastAPI(
    title="Ozone Recon RAG Diagnosis Service",
    description=(
        "Pluggable RAG pipeline for diagnosing Ozone cluster alerts and "
        "proposing (dry-run only) remediations."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allowed_origins] if settings.cors_allowed_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
