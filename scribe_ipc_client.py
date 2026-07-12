#!/usr/bin/env python3
"""IPC broker shim for Scribe claudeProcess."""
import os, sys, json, time, uuid
from pathlib import Path

IPC_BASE = Path.home() / ".forge" / "scratch" / "t-scribe-mgr" / "pipe"
REQUEST_DIR = IPC_BASE / "req"
RESPONSE_DIR = IPC_BASE / "resp"
REQUEST_TIMEOUT_S = 180

def install_ipc_backend():
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
    import claudeProcess
    claudeProcess._client = lambda: type("Stub", (), {})()
    original_extract_json = claudeProcess._extract_json
    claudeProcess._claude_json = lambda m, s, u, mt, t: _ipc_claude_json(m, s, u, mt, t, original_extract_json)

def _ipc_claude_json(model, system, user, max_tokens, temperature, extract_json_func):
    req_id = str(uuid.uuid4())
    req_path = REQUEST_DIR / f"{req_id}.json"
    resp_path = RESPONSE_DIR / f"{req_id}.json"
    req_payload = {"model": model, "system": system, "user": user, "max_tokens": max_tokens, "temperature": temperature, "ts": time.time()}
    req_path.write_text(json.dumps(req_payload, indent=0))
    start = time.time()
    while time.time() - start < REQUEST_TIMEOUT_S:
        if resp_path.exists():
            try:
                resp_data = json.loads(resp_path.read_text())
                req_path.unlink(missing_ok=True)
                resp_path.unlink(missing_ok=True)
                if "content" in resp_data:
                    return extract_json_func(resp_data["content"])
                elif "error" in resp_data:
                    raise RuntimeError(f"Broker error: {resp_data['error']}")
                return resp_data
            except: pass
        time.sleep(0.1)
    raise TimeoutError(f"IPC request {req_id} timed out after {REQUEST_TIMEOUT_S}s")
