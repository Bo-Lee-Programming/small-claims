"""Record what each Otari call asked for and what the gateway answered.

Wraps a transport, so it sees exactly what went over the wire. The
Authorization header is never recorded; page text and long outputs are cut.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .otari import Transport, urllib_transport

KEEP_HEADERS = ("otari-guardrails", "otari-end-user-budget", "otari-error-code",
                "otari-container-id", "otari-error-retryable")


def _cut(value, limit=300):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


class EvidenceLog:
    def __init__(self, transport: Transport | None = None):
        self.inner = transport or urllib_transport
        self.entries: list[dict] = []

    def __call__(self, method, url, headers, body):
        started = datetime.now(timezone.utc)
        status, resp_headers, raw = self.inner(method, url, headers, body)
        req = json.loads(body) if body else {}
        try:
            resp = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            resp = {}
        self.entries.append({
            "at": started.isoformat(timespec="seconds"),
            "call": f"{method} {url.split('/api/v1', 1)[-1]}",
            "status": status,
            "request": _summarize_request(req),
            "response": _summarize_response(resp),
            "headers": {k.lower(): v for k, v in resp_headers.items() if k.lower() in KEEP_HEADERS},
        })
        return status, resp_headers, raw

    def features(self) -> dict[str, bool]:
        """Which rubric features this run demonstrably exercised (status 2xx)."""
        ok = [e for e in self.entries if 200 <= e["status"] < 300]
        tool_types = {c.get("type") for e in ok for c in e["response"].get("tool_calls", [])}
        served = {e["response"].get("model") for e in ok if e["response"].get("model")}
        roles = {e["request"].get("model") for e in ok if e["request"].get("model")}
        return {
            "web fetch": any(e["call"].endswith("/fetch") for e in ok),
            "web search": "web_search" in tool_types,
            "code execution": any(e["call"].endswith("/code/execute") for e in ok),
            "MCP": "mcp" in tool_types,
            "guardrails": any("otari-guardrails" in e["headers"] for e in ok),
            "routing (policy name served by a real model)": any(
                e["request"].get("model") and e["response"].get("model")
                and e["request"]["model"] != e["response"]["model"] for e in ok),
            "multiple models, distinct roles": len(roles) >= 2 and len(served) >= 2,
            "per-user (end-user) attribution": all(e["request"].get("user") for e in ok
                                                   if e["call"].endswith("/completions")),
            "budget read": any(e["call"].endswith("/key/budget") for e in ok),
            "spend cap in force": any(e["call"].endswith("/key/budget")
                                      and e["response"].get("remaining_usd") is not None
                                      for e in ok),
            "end-user budget applied": any("otari-end-user-budget" in e["headers"] for e in ok),
            "budget refusal seen": any(e["headers"].get("otari-error-code") == "budget_exceeded"
                                       for e in self.entries),
        }

    def dump(self) -> dict:
        return {"features": self.features(), "calls": self.entries}


def _summarize_request(req: dict) -> dict:
    out = {k: req[k] for k in ("model", "user", "metadata", "url", "max_chars") if k in req}
    if "tools" in req:
        out["tools"] = req["tools"]
    if "guardrails" in req:
        out["guardrails"] = req["guardrails"]
    if "mcp_servers" in req:
        out["mcp_servers"] = [{"name": s.get("name"), "url": s.get("url"),
                               "allowed_tools": s.get("allowed_tools")}
                              for s in req["mcp_servers"]]
    if "code" in req:
        out["code"] = _cut(req["code"], 600)
    return out


def _summarize_response(resp: dict) -> dict:
    out = {}
    for key in ("model", "usage", "remaining_usd", "budgets_enforced", "fetch_tool",
                "final_url", "title", "truncated", "stdout", "stderr", "return_code", "detail"):
        if key in resp:
            out[key] = resp[key] if key not in ("stdout", "detail") else _cut(resp[key])
    if "text" in resp:
        out["text_chars"] = len(resp["text"])
    if resp.get("choices"):
        out["answer"] = _cut((resp["choices"][0].get("message") or {}).get("content") or "")
    if resp.get("otari_tool_calls"):
        out["tool_calls"] = [{k: (_cut(v) if k in ("output", "stdout", "code") else v)
                              for k, v in call.items()}
                             for call in resp["otari_tool_calls"]]
    return out
