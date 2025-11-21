# MCP Python Adapter (Canonical JSON)

This service acts as a thin adapter in front of your MCP Orchestrator.

It:
- Accepts **canonical JSON** from any client at `POST /canonical/voice`
- Translates it into the simple MCP `/orchestrate` request format
- Calls the MCP Orchestrator
- Wraps the response back into a canonical response envelope

## Endpoints

- `GET /health` – basic health check
- `POST /canonical/voice` – main entrypoint for canonical messages

## Expected Canonical Request (example)

```json
{
  "version": "1.0",
  "timestamp": "2025-11-21T12:44:05.112Z",
  "context": {
    "channel": "web",
    "device": "browser",
    "locale": "en-US",
    "tenant": "blinksbuy",
    "client_app": "food-order-widget"
  },
  "session": {
    "session_id": "postman-user-B:web",
    "conversation_id": "conv-23f98c83-9928-4e41-b9f4-01d4998689f2",
    "user_id": "postman-user-B",
    "turn": 7
  },
  "request": {
    "type": "text",
    "text": "Can you please get the menu?",
    "intent_override": null
  },
  "observability": {
    "trace_id": "trace-319bf86a-6688-40d5-9833-437b7e2345b2",
    "span_id": "span-adapter-in-0021"
  }
}
```

## MCP Request (internal)

The adapter converts this canonical envelope into a call to your MCP Orchestrator:

```json
{
  "text": "Can you please get the menu?",
  "user_id": "postman-user-B",
  "channel": "web",
  "session_id": "postman-user-B:web"
}
```

## Deployment on Railway

1. Create a new service from this repo.
2. Set the environment variable `MCP_URL` to your MCP Orchestrator `/orchestrate` URL.
3. Railway will install `requirements.txt` and run the `Procfile`:

   ```bash
   web: uvicorn app:app --host 0.0.0.0 --port $PORT
   ```

After deployment, you can test with `POST /canonical/voice`.
