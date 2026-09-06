from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ecg_pipeline import DEFAULT_INPUT_FS, ECGPipeline

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pyserial is installed as the "serial" module.
    serial = None
    list_ports = None


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="ECG Local Monitor")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class StartRequest(BaseModel):
    port: str
    baudrate: int = 115200


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        async with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                await client.send_text(payload)
            except RuntimeError:
                await self.disconnect(client)


class SerialECGReader:
    def __init__(self, websocket_manager: ConnectionManager) -> None:
        self.websocket_manager = websocket_manager
        self.pipeline: ECGPipeline | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.serial_conn: Any | None = None
        self.buffer: deque[float] = deque(maxlen=DEFAULT_INPUT_FS * 10)
        self.buffer_lock = threading.Lock()
        self.latest_lead_ok = False
        self.running = False
        self.last_classification_at = 0.0

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def load_pipeline(self) -> None:
        if self.pipeline is None:
            self.pipeline = ECGPipeline(BASE_DIR)

    def start(self, port: str, baudrate: int) -> None:
        if serial is None:
            raise RuntimeError("Falta instalar pyserial. Ejecuta: python -m pip install pyserial")
        if self.running:
            raise RuntimeError("La lectura serial ya esta activa")
        self.load_pipeline()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._read_loop, args=(port, baudrate), daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.serial_conn is not None:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.running = False
        self._send_status(False, "Lectura serial detenida")

    def _read_loop(self, port: str, baudrate: int) -> None:
        try:
            self.serial_conn = serial.Serial(port=port, baudrate=baudrate, timeout=1)
            self.running = True
            self._send_status(True, f"Serial conectado en {port}")
            while not self.stop_event.is_set():
                raw_line = self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
                if not raw_line or raw_line.startswith("#"):
                    continue
                sample = self._parse_sample(raw_line)
                if sample is None:
                    continue
                with self.buffer_lock:
                    self.buffer.append(float(sample["ecg_raw"]))
                    self.latest_lead_ok = bool(sample["lead_ok"])
                self._broadcast(sample)
                self._maybe_classify()
        except Exception as exc:
            self.running = False
            self._send_error(f"Error serial: {exc}")
        finally:
            self.running = False
            if self.serial_conn is not None:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            self.serial_conn = None

    @staticmethod
    def _parse_sample(line: str) -> dict[str, Any] | None:
        parts = line.split(",")
        if len(parts) != 4:
            return None
        try:
            return {
                "type": "sample",
                "time_ms": int(float(parts[0])),
                "ecg_raw": int(float(parts[1])),
                "ecg_volt": float(parts[2]),
                "lead_ok": parts[3].strip() == "1",
            }
        except ValueError:
            return None

    def _maybe_classify(self) -> None:
        now = time.monotonic()
        if now - self.last_classification_at < 1.0:
            return
        self.last_classification_at = now
        with self.buffer_lock:
            lead_ok = self.latest_lead_ok
            data = list(self.buffer)
        if not lead_ok:
            self._broadcast(
                {
                    "type": "classification",
                    "bpm": None,
                    "model_class": None,
                    "model_label": "Pausado",
                    "rate_label": "Electrodos desconectados",
                    "confidence": None,
                    "message": "Electrodos desconectados",
                }
            )
            return
        if self.pipeline is None:
            self._send_error("Modelo no cargado")
            return
        result = self.pipeline.classify_segment(data, fs=DEFAULT_INPUT_FS)
        self._broadcast(
            {
                "type": "classification",
                "bpm": result.bpm,
                "model_class": result.model_class,
                "model_label": result.model_label,
                "rate_label": result.rate_label,
                "confidence": result.confidence,
                "beats_detected": result.beats_detected,
                "message": result.message,
            }
        )

    def _broadcast(self, message: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.websocket_manager.broadcast(message), self.loop)

    def _send_status(self, connected: bool, message: str) -> None:
        self._broadcast({"type": "status", "connected": connected, "message": message})

    def _send_error(self, message: str) -> None:
        self._broadcast({"type": "error", "connected": False, "message": message})


connections = ConnectionManager()
reader = SerialECGReader(connections)


@app.on_event("startup")
async def startup_event() -> None:
    reader.set_loop(asyncio.get_running_loop())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/ports")
async def ports() -> dict[str, Any]:
    if list_ports is None:
        return {"ports": [], "error": "Falta instalar pyserial. Ejecuta: python -m pip install pyserial"}
    return {
        "ports": [
            {"device": port.device, "description": port.description}
            for port in list_ports.comports()
        ]
    }


@app.post("/api/start")
async def start(request: StartRequest) -> dict[str, Any]:
    if not request.port.strip():
        raise HTTPException(status_code=400, detail="Selecciona un puerto COM")
    try:
        reader.start(request.port.strip(), request.baudrate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/stop")
async def stop() -> dict[str, Any]:
    reader.stop()
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await connections.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await connections.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
