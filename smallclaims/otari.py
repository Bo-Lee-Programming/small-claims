"""Minimal client for the hosted Otari gateway (https://otari.ai), stdlib only.

The API key is read from OTARI_API_KEY and is never printed, logged or written
to disk. Every call returns the parsed JSON body together with the response
headers, because Otari reports guardrail verdicts, the end user's budget and
errors in headers.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.otari.ai/api/v1"

# (method, url, headers, body bytes or None) -> (status, headers, body bytes)
Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     timeout: float = 180.0) -> tuple[int, dict, bytes]:
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except HTTPError as err:
        return err.code, dict(err.headers.items()), err.read()
    except URLError as err:
        raise OtariError(0, "network", str(err.reason)) from None


class OtariError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}

    @property
    def budget_exceeded(self) -> bool:
        return self.code == "budget_exceeded"

    @property
    def guardrail_blocked(self) -> bool:
        return self.code == "guardrail_violation"


@dataclass
class Reply:
    body: dict
    headers: dict = field(default_factory=dict)

    def header(self, name: str, default: str = "") -> str:
        lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lower:
                return value
        return default

    @property
    def text(self) -> str:
        choices = self.body.get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content") or ""

    @property
    def cost(self) -> float:
        """Inline cost Otari reports (usage.cost_usd, or the Otari-Cost-USD header)."""
        raw = (self.body.get("usage") or {}).get("cost_usd") or self.header("Otari-Cost-USD")
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    @property
    def served_model(self) -> str:
        """Which model actually answered: Otari-Model header, else the body's model."""
        return self.header("Otari-Model") or self.body.get("model", "")

    @property
    def attempts(self) -> int:
        try:
            return int(self.header("Otari-Attempt-Count") or 1)
        except ValueError:
            return 1

    @property
    def tool_calls(self) -> list[dict]:
        return self.body.get("otari_tool_calls") or []

    @property
    def guardrails(self) -> list[dict]:
        raw = self.header("Otari-Guardrails")
        try:
            return json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []


class OtariClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str | None = None,
                 transport: Transport | None = None, end_user_budget: str | None = None,
                 send_user: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OTARI_API_KEY", "")
        self.transport = transport or urllib_transport
        self.end_user_budget = end_user_budget
        self.send_user = send_user

    # -- plumbing ---------------------------------------------------------
    def request(self, method: str, path: str, body: dict | None = None,
                headers: dict | None = None) -> Reply:
        if not self.api_key:
            raise OtariError(0, "no_key", "set OTARI_API_KEY (an otk_... key from otari.ai)")
        all_headers = {"Authorization": f"Bearer {self.api_key}",
                       "Content-Type": "application/json",
                       "User-Agent": "small-claims/0.1"}
        if self.end_user_budget:
            all_headers["Otari-End-User-Budget"] = self.end_user_budget
        all_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        status, resp_headers, raw = self.transport(method, self.base_url + path, all_headers, data)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500].decode("utf-8", "replace")}
        if status >= 400:
            lowered = {k.lower(): v for k, v in resp_headers.items()}
            detail = parsed.get("detail") if isinstance(parsed, dict) else None
            code = lowered.get("otari-error-code") or _error_code(parsed) or f"http_{status}"
            raise OtariError(status, code, _error_message(detail, parsed), resp_headers)
        return Reply(parsed if isinstance(parsed, dict) else {"data": parsed}, resp_headers)

    # -- endpoints --------------------------------------------------------
    def chat(self, model: str, messages: list[dict], *, user: str | None = None,
             tools: list[dict] | None = None, guardrails: list[dict] | None = None,
             mcp_servers: list[dict] | None = None, metadata: dict | None = None,
             temperature: float = 0.1, max_tokens: int = 1500) -> Reply:
        body: dict = {"model": model, "messages": messages,
                      "temperature": temperature, "max_tokens": max_tokens}
        for key, value in (("user", user if self.send_user else None), ("tools", tools), ("guardrails", guardrails),
                           ("mcp_servers", mcp_servers), ("metadata", metadata)):
            if value:
                body[key] = value
        return self.request("POST", "/chat/completions", body)

    def fetch(self, url: str, *, user: str | None = None, max_chars: int = 40000) -> Reply:
        body = {"url": url, "max_chars": max_chars}
        if user and self.send_user:
            body["user"] = user
        return self.request("POST", "/fetch", body)

    def execute(self, code: str, *, user: str | None = None, timeout_seconds: int = 30) -> Reply:
        body = {"code": code, "timeout_seconds": timeout_seconds}
        if user and self.send_user:
            body["user"] = user
        return self.request("POST", "/code/execute", body)

    def key_budget(self) -> Reply:
        return self.request("GET", "/key/budget")

    def models(self) -> Reply:
        return self.request("GET", "/models")

    def tools(self) -> Reply:
        return self.request("GET", "/tools")

    def guardrail_profiles(self) -> Reply:
        return self.request("GET", "/tool-settings/guardrails/profiles")


def _error_code(parsed) -> str:
    if not isinstance(parsed, dict):
        return ""
    detail = parsed.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    err = parsed.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or "")
    return str(parsed.get("code") or "")


def _error_message(detail, parsed) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        return str(parsed["error"].get("message") or parsed["error"])
    return json.dumps(parsed)[:300]
