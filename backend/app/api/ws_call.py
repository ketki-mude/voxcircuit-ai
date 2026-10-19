from fastapi import APIRouter, WebSocket, status

from app.calls.handler import CallHandler
from app.calls.recorder import CallRecorder
from app.config import Settings

router = APIRouter()


def origin_allowed(origin: str | None, settings: Settings) -> bool:
    # Browsers always send Origin on WebSocket upgrades, and CORS does not apply to
    # WebSockets, so this check is what stops other sites from opening calls.
    # Non-browser clients (tests, scripts) send no Origin.
    return origin is None or origin in settings.allowed_origins


@router.websocket("/ws/call")
async def ws_call(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    if not origin_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    handler = CallHandler(
        websocket=websocket,
        settings=settings,
        recorder=CallRecorder(websocket.app.state.sessionmaker),
        session_factory=websocket.app.state.live_session_factory,
    )
    await handler.run()
