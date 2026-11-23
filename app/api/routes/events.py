"""
events.py

Real-Time Event-Driven API endpoints for LifeOS:
- Server-Sent Events (SSE): GET /api/events/sse
- Full-Duplex WebSockets: WebSocket /ws/events
- Manual Event Broadcasting: POST /api/events/broadcast (Admin)

Integrates:
- Milestone 3: Device Identity and mutual trust authentication.
- Milestone 4: Granular consent redaction and cryptographic audit logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from starlette.responses import StreamingResponse

import config
from app.api.auth import AuthenticatedUser, verify_token
from app.api.dependencies import require_admin, require_auth
from app.events.bus import get_event_bus
from app.events.models import LifeOSEvent
from app.security.audit import record_audit_event
from app.security.device_identity import get_device_identity

logger = logging.getLogger("lifeos.api.events")

router = APIRouter()


def _authenticate_token_param(token: str | None) -> AuthenticatedUser | None:
    """Helper to authenticate token query parameter for clients unable to pass custom headers."""
    if not token:
        return None
    return verify_token(token)


@router.get(
    "/sse",
    summary="Server-Sent Events Stream",
    description="Stream real-time system, ingestion, query, and device events over SSE.",
)
async def sse_event_stream(
    request: Request,
    last_event_id: str | None = Query(None, alias="last_event_id"),
    topics: str | None = Query(None, description="Comma-separated topic patterns (e.g. 'ingestion.*,device.*')"),
    token: str | None = Query(None, description="Bearer token parameter for standard browser EventSource"),
    header_last_id: str | None = Header(None, alias="Last-Event-ID"),
    max_events: int | None = Query(None, description="Maximum number of events to receive before closing stream"),
    timeout: float = Query(15.0, description="Heartbeat timeout in seconds"),
):
    """
    Server-Sent Events streaming endpoint with Last-Event-ID reconnection replay
    and automatic keep-alive heartbeats.
    """
    # 1. Authentication: Check standard header first, fallback to query token
    auth_user: AuthenticatedUser | None = None
    auth_header = request.headers.get("Authorization")

    if getattr(config, "API_AUTH_ENABLED", True):
        if auth_header and auth_header.startswith("Bearer "):
            auth_user = verify_token(auth_header.split(" ", 1)[1])
        elif token:
            auth_user = _authenticate_token_param(token)

        if not auth_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: valid bearer token or query token required for SSE stream.",
            )
    else:
        auth_user = AuthenticatedUser(user_id="anonymous", role="admin")

    # 2. Parse topic filters
    filter_topics: set[str] | None = None
    if topics:
        filter_topics = {t.strip() for t in topics.split(",") if t.strip()}

    bus = get_event_bus()
    # Bind current loop to bus
    bus.set_loop(asyncio.get_running_loop())
    queue = bus.subscribe(topics=filter_topics)

    effective_last_id = last_event_id or header_last_id

    # Record audit log
    record_audit_event(
        event_type="sse_connected",
        actor=auth_user.user_id,
        resource="api/events/sse",
        purpose="client_streaming",
        status="success",
        details={"topics": list(filter_topics) if filter_topics else "*", "last_event_id": effective_last_id},
    )

    async def event_generator():
        yielded_count = 0
        try:
            # 1. Replay missed events if client specified Last-Event-ID
            if effective_last_id:
                missed = bus.get_events_since(effective_last_id)
                for evt in missed:
                    if bus._matches_topics(evt.event_type, filter_topics):
                        yield evt.to_sse()
                        yielded_count += 1
                        if max_events is not None and yielded_count >= max_events:
                            return

            # 2. Main streaming loop
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                try:
                    # Wait for next event or heartbeat timeout
                    event: LifeOSEvent = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield event.to_sse()
                    yielded_count += 1
                    if max_events is not None and yielded_count >= max_events:
                        break
                except asyncio.TimeoutError:
                    # Send keep-alive heartbeat comment
                    yield ": keep-alive\n\n"
                    if max_events is not None:
                        break
        finally:
            bus.unsubscribe(queue)
            record_audit_event(
                event_type="sse_disconnected",
                actor=auth_user.user_id,
                resource="api/events/sse",
                purpose="client_streaming",
                status="success",
                details={},
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws")
async def websocket_event_endpoint(
    websocket: WebSocket,
    token: str | None = Query(None),
):
    """
    Full-duplex WebSocket endpoint for real-time bidirectional communication.
    Supports dynamic topic subscriptions, heartbeats, and live event streaming.
    """
    # 1. Authentication check
    auth_user: AuthenticatedUser | None = None
    if getattr(config, "API_AUTH_ENABLED", True):
        auth_header = websocket.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_user = verify_token(auth_header.split(" ", 1)[1])
        elif token:
            auth_user = _authenticate_token_param(token)

        if not auth_user:
            # Reject unauthenticated connection
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    else:
        auth_user = AuthenticatedUser(user_id="anonymous", role="admin")

    await websocket.accept()
    bus = get_event_bus()
    bus.set_loop(asyncio.get_running_loop())
    queue = bus.subscribe()

    device = get_device_identity()

    # Record connection audit
    record_audit_event(
        event_type="ws_connected",
        actor=auth_user.user_id,
        resource="ws/events",
        purpose="client_streaming",
        status="success",
        details={"client": str(websocket.client)},
    )

    # Send initial welcome payload
    await websocket.send_json({
        "type": "connection.accepted",
        "device_id": device.device_id,
        "device_name": device.device_name,
        "authenticated_user": auth_user.user_id,
    })

    async def receive_loop():
        """Listen for incoming client commands (e.g. topic subscription, pings)."""
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    action = msg.get("action")
                    if action == "subscribe":
                        new_topics = set(msg.get("topics", []))
                        with bus._lock:
                            bus._subscriber_filters[queue] = new_topics
                        await websocket.send_json({"type": "subscription.updated", "topics": list(new_topics)})
                    elif action == "ping":
                        await websocket.send_json({"type": "pong", "timestamp": msg.get("timestamp")})
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass

    async def send_loop():
        """Broadcast events from the queue to the connected WebSocket client."""
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event.to_dict())
        except WebSocketDisconnect:
            pass

    try:
        # Run receive and send loops concurrently until disconnect
        receiver_task = asyncio.create_task(receive_loop())
        sender_task = asyncio.create_task(send_loop())
        done, pending = await asyncio.wait(
            [receiver_task, sender_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except Exception as exc:
        logger.debug(f"WebSocket session ended with exception: {exc}")
    finally:
        bus.unsubscribe(queue)
        record_audit_event(
            event_type="ws_disconnected",
            actor=auth_user.user_id,
            resource="ws/events",
            purpose="client_streaming",
            status="success",
            details={},
        )


@router.post(
    "/broadcast",
    summary="Broadcast Event (Admin Only)",
    description="Manually broadcast an event to all active SSE and WebSocket clients.",
)
async def broadcast_event(
    event_type: str = Query(..., description="Event type identifier"),
    payload: dict[str, Any] = ...,
    resource_path: str | None = Query(None, description="Optional resource path for consent evaluation"),
    user: AuthenticatedUser = Depends(require_admin),
):
    evt = LifeOSEvent(
        event_type=event_type,
        payload=payload,
        producer=f"admin:{user.user_id}",
        resource_path=resource_path,
    )
    bus = get_event_bus()
    await bus.publish(evt)
    return {"broadcast": True, "event_id": evt.event_id}
