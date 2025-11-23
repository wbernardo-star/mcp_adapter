#adapterAPIKeys app.py 

import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# --------- Core config ---------

MCP_URL = os.getenv("MCP_URL")  # e.g. https://your-mcp-service.up.railway.app/orchestrate
if not MCP_URL:
    print(
        "[WARN] MCP_URL env var is not set. "
        "Set it in your environment (or Railway variables) before using /canonical/voice."
    )

# --------- API key protection ---------
# You can configure:
#   - API_KEYS = "key1,key2,key3"
#   - or API_KEY = "single-key"
RAW_API_KEYS = os.getenv("API_KEYS") or os.getenv("API_KEY")
if not RAW_API_KEYS:
    raise RuntimeError(
        "API_KEYS or API_KEY environment variable must be set for API key protection. "
        "Set API_KEYS to a comma-separated list of allowed API keys, "
        "or API_KEY to a single key."
    )

API_KEYS = {k.strip() for k in RAW_API_KEYS.split(",") if k.strip()}


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")
):
    """
    Simple API key protection.

    - Expects the client to send:  X-API-Key: <key>
    - Compares it against the configured API_KEYS set.
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=401,
            detail="Missing API key header 'X-API-Key'",
        )

    if x_api_key not in API_KEYS:
        # Do not leak which specific keys exist; just say it's invalid.
        raise HTTPException(
            status_code=403,
            detail="Invalid API key",
        )

    return x_api_key


# --------- App init ---------

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


async def call_mcp(
    text: str,
    user_id: str,
    channel: str,
    session_id: str,
) -> Dict[str, Any]:
    """
    Calls the MCP /orchestrate endpoint with the minimal request body
    and returns the JSON.
    """
    if not MCP_URL:
        raise HTTPException(
            status_code=500,
            detail="MCP_URL is not configured on the adapter service",
        )

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
                detail=(
                    f"MCP orchestrator error: "
                    f"{e.response.status_code} {e.response.text[:200]}"
                ),
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


@app.post("/canonical/voice", dependencies=[Depends(require_api_key)])
async def canonical_voice(envelope: CanonicalEnvelope):
    """
    Entry point for canonical JSON from any client.

    Flow:
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

    duration_ms = (time.perf_counter() - start) * 1000.0

    # Build canonical response envelope
    canonical_response = {
        "version": envelope.version,
        "timestamp": envelope.timestamp,
        "context": envelope.context.dict(),
        "session": {
            "session_id": envelope.session.session_id,
            "conversation_id": envelope.session.conversation_id,
            "user_id": envelope.session.user_id,
            "turn": envelope.session.turn,
            "route": route,
        },
        "response": {
            "type": "text",
            "text": reply_text,
            "metadata": {
                "source": "mcp_orchestrator",
                "duration_ms": duration_ms,
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
