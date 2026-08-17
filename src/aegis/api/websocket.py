"""AEGIS WebSocket — real-time updates for dashboard."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("aegis.websocket")

router = APIRouter(tags=["websocket"])

# Connected clients
_clients: list[WebSocket] = []


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time dashboard updates."""
    await websocket.accept()
    _clients.append(websocket)
    logger.info("WebSocket client connected, total: %d", len(_clients))

    try:
        while True:
            # Keep connection alive, receive pings
            data = await websocket.receive_text()
            # Echo back or handle commands
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        _clients.remove(websocket)
        logger.info("WebSocket client disconnected, total: %d", len(_clients))
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        if websocket in _clients:
            _clients.remove(websocket)


async def broadcast(data: dict[str, Any]) -> None:
    """Broadcast data to all connected WebSocket clients."""
    if not _clients:
        return

    message = json.dumps(data, default=str)
    disconnected = []

    for ws in _clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        _clients.remove(ws)


def get_client_count() -> int:
    """Get number of connected WebSocket clients."""
    return len(_clients)
