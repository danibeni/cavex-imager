#!/usr/bin/env bash
# Quick smoke test for CAVEX Imager API.
# Start the service first: ./scripts/run_dev.sh
set -e
BASE="${BASE_URL:-http://127.0.0.1:8000}"
JQ=$(command -v jq 2>/dev/null || true)
if [ -n "$JQ" ]; then
  pretty() { "$JQ" .; }
else
  pretty() { cat; echo; }
fi

echo "=== Root ==="
curl -sS "${BASE}/" | pretty

echo "=== Health ==="
curl -sS "${BASE}/api/v1/health" | pretty

echo "=== Lease status (no lease) ==="
curl -sS "${BASE}/api/v1/lease/status" | pretty

echo "=== Acquire lease ==="
curl -sS -X POST "${BASE}/api/v1/lease/acquire" \
  -H "Content-Type: application/json" \
  -d '{"owner":"test_client","priority":50,"ttl_seconds":120}' | pretty

echo "=== Lease status (with lease) ==="
curl -sS "${BASE}/api/v1/lease/status" | pretty

LEASE_ID=$(curl -sS "${BASE}/api/v1/lease/status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('lease_id',''))")
echo "=== Renew lease (lease_id=${LEASE_ID}) ==="
curl -sS -X POST "${BASE}/api/v1/lease/renew" \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\":\"${LEASE_ID}\",\"ttl_seconds\":120}" | pretty

echo "=== Diag summary ==="
curl -sS "${BASE}/api/v1/diag/summary" | pretty

echo "=== Diag metrics (first 20 lines) ==="
curl -sS "${BASE}/api/v1/diag/metrics" | head -20

echo "=== Release lease ==="
curl -sS -X POST "${BASE}/api/v1/lease/release" \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\":\"${LEASE_ID}\"}" | pretty

echo "Done."
