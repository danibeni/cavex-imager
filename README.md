# cavex_imager

REST + WebSocket microservice for controlling an astronomical camera via [INDIGO](http://www.indigo-astronomy.org/). Part of the **CAVEX_V2** project (atmospheric extinction monitor at Calar Alto Observatory).

## Purpose

`cavex_imager` provides:

- **Camera control**: Connect/disconnect device, set ROI, binning, cooler, exposure time
- **Image capture**: Single exposures and periodic sequences; abort in progress
- **Concurrency**: Priority-based **lease** system; higher-priority clients can preempt
- **Storage**: Session paths, FITS files from INDIGO, and **sidecar JSON** metadata
- **Telemetry**: WebSocket stream (1–2 Hz) with camera state and events
- **Observability**: Health/diag endpoints, Prometheus metrics, structured logging

Scientific analysis, night orchestration, and hut control are handled by other services (`cavex_analysis`, `cavex_manager`, `cavex_hut`).

## Tech Stack

- **Python 3.11+**
- **FastAPI** (async web framework)
- **INDIGO** (astronomical device protocol)
- **Astropy** (FITS validation)

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- INDIGO server running (e.g. `indigo_server` with CCD driver) on host, typically TCP 7624

### Install

```bash
cd cavex_imager
poetry install
```

### Run

```bash
poetry run uvicorn src.main:app --host 0.0.0.0 --port 8000
```

### Test

```bash
poetry run pytest
```

## Configuration

Configuration is loaded from YAML (`config/default.yaml`, overridable by `config/production.yaml`) and environment variables.

| Section | Purpose |
|--------|---------|
| `service` | name, version, log_level |
| `indigo` | host, port, device_name, reconnect |
| `auth` | enabled, API keys |
| `lease` | preempt_mode, TTL limits |
| `storage` | base paths, disk checks |
| `logging` | stdout, file rotation |

Example env overrides:

```bash
LOG_LEVEL=DEBUG
INDIGO_HOST=172.17.0.1
INDIGO_PORT=7624
```

## API Overview

| Area | Endpoints |
|------|-----------|
| **Health** | `GET /api/v1/health`, `GET /api/v1/diag/summary` |
| **Lease** | `POST /api/v1/lease/acquire`, `POST /renew`, `POST /release`, `GET /status` |
| **Camera** | `POST /connect`, `POST /disconnect`, `POST /config`, `POST /exposure/start`, `POST /exposure/abort`, `GET /state` |
| **Telemetry** | WebSocket `WS /api/v1/ws/telemetry?rate_hz=1` |

Typical flow: `POST /lease/acquire` → `POST /camera/connect` → `POST /camera/local_mode` (session path) → `POST /camera/exposure/start`.

## Project Structure

```
cavex_imager/
├── config/
│   ├── default.yaml
│   └── production.yaml
├── src/
│   ├── main.py              # FastAPI app, lifespan
│   ├── domain/              # Models, ports, exceptions
│   ├── application/         # CaptureService, LeaseManager
│   ├── infrastructure/      # IndigoCameraAdapter, SidecarWriter, FitsValidator
│   ├── presentation/        # REST routes, WebSocket
│   └── shared/              # Config, logging, observability
├── tests/
├── docs/
│   ├── CAVEX_IMAGER_Architecture_Spec_v1.3.md
│   ├── CAVEX_IMAGER_Technical_Spec_v1.3.md
│   └── SERVICE_OVERVIEW.md
└── pyproject.toml
```

## Docker

```bash
docker build -t caha/cavex_imager:1.0.0 .
docker run -p 8001:8000 \
  -e INDIGO_HOST=172.17.0.1 \
  -e INDIGO_PORT=7624 \
  -v /opt/cavex/data:/data \
  caha/cavex_imager:1.0.0
```

## Documentation

- **[Architecture Spec](docs/CAVEX_IMAGER_Architecture_Spec_v1.3.md)** – Full architecture, requirements, deployment
- **[Technical Spec](docs/CAVEX_IMAGER_Technical_Spec_v1.3.md)** – API details, schemas, workflows
- **[Service Overview](docs/SERVICE_OVERVIEW.md)** – Component overview, extension guide

## License

This project is free software, released under a license compatible with [INDIGO Astronomy](http://www.indigo-astronomy.org/) (BSD-style). See the repository for the full license text.

## Development Team

**CAHA Staff**
- Daniel Benítez – dbenitez@caha.es
- Julio Marín – jmarin@caha.es
- Juan Francisco López – jfran@caha.es

**UAL members**
- Raúl Ortega Pérez – rop462@inlumine.ual.es
- Juanjo Moreno – juanjomoreno@ual.es
- Vicente González Ruiz – vruiz@ual.es

**Contact:** Calar Alto Observatory · cavex-dev@caha.es
