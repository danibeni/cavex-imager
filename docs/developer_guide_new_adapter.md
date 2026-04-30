# Guía de desarrollador — Añadir un nuevo adaptador de instrumento

Este documento explica cómo extender CAVEX Imager para soportar un nuevo tipo de
instrumento (por ejemplo, una montura, un espectrógrafo o una cámara guía) sin
modificar la lógica de negocio existente.

La arquitectura sigue el patrón **Ports & Adapters** (también llamado Hexagonal):

```
┌─────────────────────────────────────────────────────────┐
│                      Dominio                            │
│   modelos · eventos · excepciones · puertos (ABC)       │
└────────────────────┬────────────────────────────────────┘
                     │ depende de
┌────────────────────▼────────────────────────────────────┐
│                   Aplicación                            │
│   CaptureService · LeaseManager                        │
└────────────────────┬────────────────────────────────────┘
                     │ inyección de dependencias
┌────────────────────▼────────────────────────────────────┐
│                Infraestructura                          │
│   IndigoCameraAdapter · SidecarWriter · FitsValidator   │
└─────────────────────────────────────────────────────────┘
```

La regla de oro es: **el dominio y la aplicación nunca importan clases concretas
de infraestructura**. Los servicios de aplicación solo conocen las interfaces
abstractas definidas en `src/domain/ports/`.

---

## 1. Puertos disponibles

Un puerto es una interfaz abstracta (`ABC`) que define el contrato que cualquier
adaptador debe cumplir. Los puertos existentes son:

| Puerto           |               Fichero               |          Para qué sirve     |
|------------------|--------------------|----------------|
| `ICameraDevice`  | `src/domain/ports/camera_device.py` | Control de una cámara CCD/CMOS |
| `IStorage`       | `src/domain/ports/storage.py`       | Escritura de sidecars y validación de disco |
| `IEventPublisher`| `src/domain/ports/event_publisher.py`| Bus de eventos interno |
| `IImageValidator`| `src/domain/ports/image_validator.py`| Validación de imágenes FITS |
| `ITelemetryPublisher`| `src/domain/ports/telemetry.py` | Difusión de telemetría WebSocket |

Para añadir un instrumento completamente nuevo (por ejemplo, una montura) habría
que definir primero un puerto nuevo (ver sección 5).

---

## 2. Caso más común: nueva cámara con protocolo distinto

Si el nuevo instrumento es una cámara pero usa un protocolo diferente a INDIGO
(por ejemplo, ASCOM, ZWO SDK nativo, o un simulador para tests), solo hay que
implementar `ICameraDevice`.

### 2.1 Crear el fichero del adaptador

```
src/infrastructure/adapters/mi_camara_adapter.py
```

### 2.2 Esqueleto mínimo

```python
"""Adaptador para MiCamara usando el protocolo XYZ."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from src.domain.ports.camera_device import CameraStateSnapshot, ICameraDevice


class MiCamaraAdapter(ICameraDevice):
    """Adaptador para cámaras XYZ."""

    def __init__(self, device_name: str) -> None:
        self._device_name = device_name
        self._connected = False
        # Estado interno cacheado (se actualiza al recibir eventos del driver)
        self._exposure_state = "IDLE"
        self._ccd_temp_c = 0.0
        self._gain: int | None = None
        self._offset: int | None = None
        self._bin_x = 1
        self._bin_y = 1
        self._roi: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._last_image_path: str | None = None
        self._local_mode_dir: str | None = None
        # Listas de callbacks suscritos por CaptureService
        self._exposure_state_handlers: list[Callable] = []
        self._image_received_handlers: list[Callable] = []

    # ── Conexión ────────────────────────────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        """Conectar al servidor / driver del instrumento."""
        # TODO: abrir socket, inicializar SDK, etc.
        self._connected = True

    async def disconnect(self) -> None:
        """Desconectar del instrumento."""
        self._connected = False

    async def connect_device(self) -> None:
        """Enviar ORDER de conexión al dispositivo físico."""
        # Para drivers que tienen un paso de "conectar dispositivo" separado
        pass

    async def disconnect_device(self) -> None:
        """Enviar ORDER de desconexión al dispositivo físico."""
        pass

    # ── Configuración ───────────────────────────────────────────────────────

    async def set_config(self, config: dict[str, Any]) -> None:
        """Aplicar configuración de captura (binning, gain, ROI…)."""
        if "bin_x" in config:
            self._bin_x = config["bin_x"]
        if "bin_y" in config:
            self._bin_y = config["bin_y"]
        if "gain" in config:
            self._gain = config["gain"]
        if "offset" in config:
            self._offset = config["offset"]
        if "roi" in config:
            self._roi = tuple(config["roi"])
        # TODO: enviar al driver real

    async def set_local_mode(self, directory: str, prefix: str) -> None:
        """Configurar directorio y prefijo para guardado local."""
        self._local_mode_dir = directory
        # TODO: enviar al driver real

    # ── Exposición ─────────────────────────────────────────────────────────

    async def start_exposure(self, exptime_s: float) -> None:
        """Iniciar una exposición. Debe ser NO bloqueante."""
        # TODO: enviar comando al driver
        # Cuando el driver señale que terminó, llamar a los handlers:
        #   await self._notify_exposure_state_changed("Busy", "Ok")
        #   await self._notify_image_received("/ruta/al/archivo.fits")
        pass

    async def abort_exposure(self) -> None:
        """Abortar la exposición en curso."""
        # TODO: enviar comando de abort al driver
        pass

    # ── Estado ─────────────────────────────────────────────────────────────

    def get_state(self) -> CameraStateSnapshot:
        """Devolver snapshot del estado actual (leído del caché interno)."""
        return CameraStateSnapshot(
            device_name=self._device_name,
            connected=self._connected,
            indigo_connected=self._connected,
            exposure_state=self._exposure_state,
            exposure_value=0.0,
            exposure_target=0.0,
            ccd_temp_c=self._ccd_temp_c,
            cooler_target_c=self._ccd_temp_c,
            cooler_power_pct=0.0,
            cooler_on=False,
            cooler_state="OFF",
            bin_x=self._bin_x,
            bin_y=self._bin_y,
            roi=self._roi,
            gain=self._gain,
            offset=self._offset,
            last_image_path=self._last_image_path,
            local_mode_dir=self._local_mode_dir,
            last_update=datetime.now(timezone.utc),
        )

    # ── Suscripción a eventos ───────────────────────────────────────────────

    def subscribe_to_exposure_events(
        self,
        on_state_changed: Callable[[str, str], Awaitable[None]] | None = None,
        on_image_received: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """CaptureService llama a esto en su __init__ para recibir eventos."""
        if on_state_changed is not None:
            self._exposure_state_handlers.append(on_state_changed)
        if on_image_received is not None:
            self._image_received_handlers.append(on_image_received)

    def subscribe_to_thermal_events(
        self,
        on_temperature_changed: Callable | None = None,
        on_cooler_state_changed: Callable | None = None,
    ) -> None:
        """Opcional: conectar callbacks de temperatura al servicio de telemetría."""
        pass  # Implementar si el instrumento tiene control térmico

    # ── Helpers privados ────────────────────────────────────────────────────

    async def _notify_exposure_state_changed(
        self, previous: str, current: str
    ) -> None:
        """Llamar cuando el driver señale un cambio de estado de exposición."""
        self._exposure_state = current
        for handler in self._exposure_state_handlers:
            await handler(previous, current)

    async def _notify_image_received(self, file_path: str) -> None:
        """Llamar cuando el driver haya escrito la imagen en disco."""
        self._last_image_path = file_path
        for handler in self._image_received_handlers:
            await handler(file_path)
```

---

## 3. Registrar el adaptador en la aplicación

Toda la composición de dependencias está en `src/main.py`, dentro de la función
`lifespan`. Para usar el nuevo adaptador, basta con sustituir la línea donde se
crea `IndigoCameraAdapter`:

```python
# Antes (INDIGO):
from src.infrastructure.adapters.indigo_camera_adapter import IndigoCameraAdapter
camera = IndigoCameraAdapter(device_name="QHY600M")

# Después (nuevo adaptador):
from src.infrastructure.adapters.mi_camara_adapter import MiCamaraAdapter
camera = MiCamaraAdapter(device_name="MiCamara")
```

El resto del código — `CaptureService`, `TelemetryHandler`, rutas HTTP — no
cambia, porque todos trabajan contra la interfaz `ICameraDevice`, no contra la
clase concreta.

---

## 4. Escribir tests para el nuevo adaptador

Crea el fichero de tests en:

```
tests/unit/infrastructure/adapters/test_mi_camara_adapter.py
```

El patrón es el mismo que en `test_indigo_camera_adapter.py`: crear el adaptador,
sustituir su cliente interno por un mock, y verificar que los callbacks se
disparan correctamente:

```python
import pytest
from unittest.mock import AsyncMock
from src.infrastructure.adapters.mi_camara_adapter import MiCamaraAdapter


def _make_adapter() -> MiCamaraAdapter:
    return MiCamaraAdapter(device_name="TestCam")


@pytest.mark.asyncio
async def test_subscribe_and_notify_state_changed() -> None:
    adapter = _make_adapter()
    on_state = AsyncMock()
    adapter.subscribe_to_exposure_events(on_state_changed=on_state)

    await adapter._notify_exposure_state_changed("Busy", "Ok")

    on_state.assert_awaited_once_with("Busy", "Ok")


@pytest.mark.asyncio
async def test_subscribe_and_notify_image_received() -> None:
    adapter = _make_adapter()
    on_image = AsyncMock()
    adapter.subscribe_to_exposure_events(on_image_received=on_image)

    await adapter._notify_image_received("/data/test.fits")

    on_image.assert_awaited_once_with("/data/test.fits")


def test_get_state_returns_snapshot() -> None:
    adapter = _make_adapter()
    state = adapter.get_state()

    assert state.device_name == "TestCam"
    assert state.connected is False
    assert state.exposure_state == "IDLE"
```

---

## 5. Añadir soporte para un tipo de instrumento completamente nuevo

Si el instrumento no es una cámara (por ejemplo, una montura AltAz), hay que
seguir estos pasos adicionales:

### 5.1 Definir el puerto en el dominio

```
src/domain/ports/mount_device.py
```

```python
from abc import ABC, abstractmethod

class IMountDevice(ABC):

    @abstractmethod
    async def slew_to(self, ra: float, dec: float) -> None:
        """Mover la montura a las coordenadas indicadas."""

    @abstractmethod
    async def abort_slew(self) -> None:
        """Detener el movimiento en curso."""

    @abstractmethod
    def get_position(self) -> tuple[float, float]:
        """Devolver posición actual (RA, Dec) en grados."""
```

### 5.2 Implementar el adaptador

```
src/infrastructure/adapters/indigo_mount_adapter.py
```

Implementar `IMountDevice` exactamente igual que en el ejemplo de la sección 2.

### 5.3 Crear el servicio de aplicación (si hace falta)

Si el instrumento tiene lógica propia (secuencias de movimiento, coordinación con
capturas), crear un nuevo servicio en `src/application/services/mount_service.py`
que reciba `IMountDevice` por el constructor (inyección de dependencias).

### 5.4 Añadir rutas HTTP

Crear el router en `src/presentation/api/routes/mount.py` y registrarlo en
`main.py`:

```python
from src.presentation.api.routes.mount import router as mount_router
app.include_router(mount_router, prefix="/api/v1/mount")
```

---

## 6. Resumen del flujo completo

```
Driver externo
      │  eventos (estado, imagen, temperatura…)
      ▼
MiCamaraAdapter          ← implementa ICameraDevice
      │  llama a handlers suscritos
      ▼
CaptureService           ← solo conoce ICameraDevice
      │  publica eventos de dominio
      ▼
InternalEventBus         ← implementa IEventPublisher
      │
      ├──▶ TelemetryHandler  → WebSocket → Frontend
      └──▶ Rutas HTTP        → respuestas REST
```

El adaptador es el único componente que sabe hablar con el hardware. Todo lo
demás es agnóstico al protocolo.

---
## 5. Añadir soporte para un tipo de instrumento completamente nuevo

## Checklist para una PR de nuevo adaptador

- [ ] Fichero en `src/infrastructure/adapters/`
- [ ] Implementa todos los métodos abstractos del puerto (`ICameraDevice` u otro)
- [ ] `get_state()` devuelve un snapshot coherente aunque el dispositivo esté desconectado
- [ ] Los callbacks de evento (`_notify_*`) se disparan en el momento correcto
- [ ] Tests unitarios en `tests/unit/infrastructure/adapters/`
- [ ] Registrado en `src/main.py`
- [ ] `poetry run pytest tests/unit/ -q` pasa al 100%
