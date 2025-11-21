import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

MCP_URL = os.getenv("MCP_URL")  # e.g. https://your-mcp-service.up.railway.app/orchestrate
if not MCP_URL:
    print("[WARN] MCP_URL env var is not set. Set it in Railway variables before using /canonical/voice.")

app = FastAPI(title="MCP Python Adapter (Canonical JSON)")


# --------- Canonical request models ---------


class CanonicalContext(BaseModel):
    channel: Optional[str] = "web"
    device: Optional[str] = None
    locale: Optional[str] = None
    tenant: Optional[str] = None
    client_app: Optional[str] = None


class CanonicalSession(BaseModel):
    session_id: str
    conversation_id: Optional[str] = None
    user_id: str
    turn: Optional[int] = None


class CanonicalRequestBody(BaseModel):
    type: str = "text"
    text: str
    intent_override: Optional[str] = None


class CanonicalObservability(BaseModel):
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


class CanonicalEnvelope(BaseModel):
    version: str = "1.0"
    timestamp: Optional[str] = None
    context: CanonicalContext
    session: CanonicalSession
    request: CanonicalRequestBody
    observability: Optional[CanonicalObservability] = None


# --------- Helper: call MCP orchestrator ---------


async def call_mcp(text: str, user_id: str, channel: str, session_id: str) -> Dict[str, Any]:
    """
    Calls the MCP /orchestrate endpoint with the minimal request body
    and returns the JSON.
    """
    if not MCP_URL:
        raise HTTPException(status_code=500, detail="MCP_URL is not configured on the adapter service")

    payload = {
        "text": text,
        "user_id": user_id,
        "channel": channel,
        "session_id": session_id,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(MCP_URL, json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"MCP orchestrator error: {e.response.status_code} {e.response.text[:200]}",
            )

        return resp.json()


# --------- Routes ---------


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "mcp_adapter_python",
        "mcp_url": MCP_URL,
    }


@app.post("/canonical/voice")
async def canonical_voice(envelope: CanonicalEnvelope):
    """
    Entry point for canonical JSON from any client.

    - Validates the canonical structure
    - Maps to MCP request
    - Calls MCP /orchestrate
    - Wraps response in a canonical response envelope
    """
    start = time.perf_counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Ensure timestamp is set
    if not envelope.timestamp:
        envelope.timestamp = now_iso

    # Extract fields for MCP call
    text = envelope.request.text
    user_id = envelope.session.user_id
    session_id = envelope.session.session_id
    channel = envelope.context.channel or "web"

    # Call MCP orchestrator
    mcp_json = await call_mcp(
        text=text,
        user_id=user_id,
        channel=channel,
        session_id=session_id,
    )

    # Expected MCP reply:
    # { "decision": "reply", "reply_text": "...", "session_id": "...", "route": "menu" }
    reply_text = mcp_json.get("reply_text") or ""
    route = mcp_json.get("route", "unknown")

    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)

    canonical_response = {
        "version": envelope.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response": {
            "status": "success",
            "code": 200,
            "message_type": "reply",
            "route": route,
            "latency_ms": elapsed_ms,
        },
        "session": {
            "session_id": envelope.session.session_id,
            "user_id": envelope.session.user_id,
            "turn": envelope.session.turn,
            "locale": envelope.context.locale,
            "tenant": envelope.context.tenant,
        },
        "payload": {
            "type": "text",
            "text": reply_text,
            "metadata": {
                "source": "mcp_orchestrator",
            },
        },
        "observability": {
            "trace_id": (
                envelope.observability.trace_id
                if envelope.observability and envelope.observability.trace_id
                else None
            ),
        },
    }

    return canonical_response
