# How to Verify the Event-Driven INDIGO Client

This document describes how to check that the Phase 1 implementation of `indigo_client.py` works correctly.

---

## 1. Unit tests (no INDIGO server needed)

Run the client unit tests. All must pass.

```bash
cd cavex_imager
poetry run pytest tests/unit/infrastructure/adapters/test_indigo_client.py -v
```

**What is checked:**
- XML parsing: `def*Vector`, `set*Vector`, `deleteProperty`, `message`, `switchProtocol`
- Extraction of `target` attribute from `<oneNumber>`
- PropertyCache: define, update merge, delete (single and all device)
- Callback registration and dispatch with device/property filters
- Command sending: `send_number`, `send_switch`, `send_text`, `enable_blob`
- Buffer extraction and self-closing tags

---

## 2. Integration tests (INDIGO server required)

You need a running INDIGO server with the **CCD Imager Simulator** (or another CCD device). Default: `localhost:7624`, device name `CCD Imager Simulator`.

**Start INDIGO server** (if you use indigo_sky or indigo_server):

```bash
# Example: indigo_sky or your usual INDIGO server
# Ensure CCD Imager Simulator driver is loaded and listening on port 7624
```

**Run integration tests:**

```bash
export CAVEX_RUN_INDIGO_INTEGRATION=true
# Optional: override host, port, device name
# export INDIGO_HOST=localhost
# export INDIGO_PORT=7624
# export INDIGO_DEVICE_NAME="CCD Imager Simulator"

poetry run pytest tests/integration/test_indigo_camera_adapter.py -v
```

These tests use the full stack: `IndigoClient` + `IndigoCameraAdapter` (which still uses `event_stream()` until Phase 2). If they pass, the client works with a real server.

---

## 3. Manual script (quick smoke test)

A small script connects to INDIGO, registers callbacks, and prints received events for a few seconds. Useful to confirm that definitions and updates are pushed correctly.

**Requirements:** INDIGO server running on `localhost:7624` (or set `INDIGO_HOST` / `INDIGO_PORT`).

```bash
# From project root
INDIGO_HOST=${INDIGO_HOST:-localhost} INDIGO_PORT=${INDIGO_PORT:-7624} \
  poetry run python scripts/verify_indigo_client.py
```

See `scripts/verify_indigo_client.py` for what it does (connect, register on_define/on_update/on_delete, print events, then disconnect).

---

## 4. Run the application

If the cavex_imager service is configured to use the same INDIGO host/port/device, start the API and use the camera endpoints (connect, get state, start exposure, etc.). Observing correct behaviour and logs confirms the client in production use.

---

## Summary

| Check              | Command / action                                      | Needs INDIGO server |
|--------------------|--------------------------------------------------------|---------------------|
| Unit tests         | `pytest tests/unit/.../test_indigo_client.py -v`        | No                  |
| Integration tests  | `CAVEX_RUN_INDIGO_INTEGRATION=true pytest tests/integration/... -v` | Yes                 |
| Manual script      | `poetry run python scripts/verify_indigo_client.py`    | Yes                 |
| Full application   | Start API and use camera endpoints                     | Yes                 |
