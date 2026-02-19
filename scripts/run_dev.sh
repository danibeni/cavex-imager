#!/usr/bin/env bash
# Run CAVEX Imager API for local testing (logs under ./logs, no /data required)
set -e
cd "$(dirname "$0")/.."
mkdir -p logs
export CAVEX_LOGGING_FILE_PATH="${CAVEX_LOGGING_FILE_PATH:-./logs/cavex_imager.log}"
export CAVEX_STORAGE_HOST_BASE_PATH="${CAVEX_STORAGE_HOST_BASE_PATH:-./data}"
mkdir -p ./data
echo "Starting server at http://127.0.0.1:8000 (logs: $CAVEX_LOGGING_FILE_PATH)"
exec poetry run uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
