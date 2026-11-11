"""Small Claims web server, stdlib only.

  python -m smallclaims                  live on otari.ai if OTARI_API_KEY is set, else demo
  python -m smallclaims --mock           demo mode: scripted court, no key, no cost
  python -m smallclaims --port 8765 --daily-trials 3 --fee-cap 0.05
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .court import DEFAULT_CAST, Trial
from .evidence import EvidenceLog
from .mock import MockOtari
from .otari import OtariClient, OtariError, urllib_transport

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "static" / "index.html"
DATA = ROOT / "data"
FAMILIES = {"prosecution": ("qwen", "llama", "gemma", "mistral"),
            "defense": ("deepseek", "kimi", "qwen"),
            "judge": ("glm", "kimi", "gpt-oss", "llama"),
            "clerk": ("qwen", "llama", "gemma"),
            "public_defender": ("gemma", "llama", "qwen", "mistral")}
EXAMPLES = ["Goldfish only have a three-second memory.",
            "Otari can route to more than 40 LLM providers through one endpoint.",
            "A day on Venus is longer than its year."]


def resolve_cast(cast: dict, catalog: list[str]) -> dict:
    """Keep the configured models the key can call; fill gaps by model family."""
    if not catalog:
        return cast
    out = {}
    for role, models in cast.items():
        keep = [m for m in models if "/" not in m or m in catalog]   # policies have no "/"
        if not keep:
            for fam in FAMILIES.get(role, ()):
                keep += [i for i in catalog if fam in i.lower()][:1]
                if len(keep) >= 2:
                    break
        out[role] = keep or models
    return out


class Court:
    def __init__(self, mock: bool, daily_trials: int, fee_cap: float, pace: float):
        key = os.environ.get("OTARI_API_KEY", "")
        self.mock = mock or not key
        self.daily_trials, self.fee_cap = daily_trials, fee_cap
        self.pace = pace if self.mock else 0.0
        self.lock = threading.Lock()
        self.filed: dict[tuple[str, str], int] = {}
        config = {}
        if (ROOT / "config.local.json").exists():
            config = json.loads((ROOT / "config.local.json").read_text(encoding="utf-8"))
        self.cast = {**DEFAULT_CAST, **config.get("cast", {})}
        self.end_user_budget = config.get("end_user_budget")
        if not self.mock:
            try:
                ids = [m.get("id", "") for m in self.client(EvidenceLog()).models().body.get("data", [])]
                self.cast = resolve_cast(self.cast, ids)
            except OtariError as err:
                print(f"could not read the model catalog: {err}")
        (DATA / "verdicts").mkdir(parents=True, exist_ok=True)
        (DATA / "records").mkdir(parents=True, exist_ok=True)

    def client(self, log: EvidenceLog) -> OtariClient:
        if self.mock:
            log.inner = MockOtari()
            return OtariClient(api_key="mock", transport=log)
        log.inner = urllib_transport
        return OtariClient(transport=log, end_user_budget=self.end_user_budget)

    def trials_left(self, visitor: str) -> int:
        return max(0, self.daily_trials - self.filed.get((visitor, date.today().isoformat()), 0))

    def take_slot(self, visitor: str) -> bool:
        with self.lock:
            if self.trials_left(visitor) <= 0:
                return False
            k = (visitor, date.today().isoformat())
            self.filed[k] = self.filed.get(k, 0) + 1
            return True


def clean_visitor(raw: str) -> str:
    v = re.sub(r"[^A-Za-z0-9_-]", "", raw or "")[:40]
    return v or "guest"


class Handler(BaseHTTPRequestHandler):
    court: Court = None  # set in main

    def log_message(self, fmt, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode())

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/" or path.startswith("/v/"):
            return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            visitor = clean_visitor(params.get("visitor", ""))
            return self._json({"mode": "demo" if self.court.mock else "live",
                               "visitor": visitor, "trials_left": self.court.trials_left(visitor),
                               "daily_trials": self.court.daily_trials,
                               "fee_cap": self.court.fee_cap,
                               "cast": {r: m[0] for r, m in self.court.cast.items()},
                               "examples": EXAMPLES})
        m = re.fullmatch(r"/api/(verdict|record)/([A-F0-9]{6})", path)
        if m:
            f = DATA / ("verdicts" if m.group(1) == "verdict" else "records") / f"{m.group(2)}.json"
            if f.exists():
                return self._send(200, f.read_bytes())
            return self._json({"detail": "no such case"}, 404)
        self._json({"detail": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/trial":
            return self._json({"detail": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
        except json.JSONDecodeError:
            return self._json({"detail": "bad json"}, 400)
        claim = str(body.get("claim", "")).strip()[:500]
        visitor = clean_visitor(body.get("visitor", ""))
        if not claim:
            return self._json({"detail": "empty claim"}, 400)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(event):
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()

        if not self.court.take_slot(visitor):
            return emit({"type": "denied", "visitor": visitor,
                         "reason": f"{visitor} has used today's {self.court.daily_trials} "
                                   "filings. The court reopens tomorrow."})
        log = EvidenceLog()
        trial = Trial(self.court.client(log), claim, f"visitor-{visitor}",
                      cast={k: list(v) for k, v in self.court.cast.items()},
                      fee_cap=self.court.fee_cap, pace=self.court.pace)
        verdict = None
        try:
            for event in trial.run():
                if event["type"] == "verdict":
                    verdict = {**event, "mode": "demo" if self.court.mock else "live",
                               "visitor": visitor}
                    (DATA / "verdicts" / f"{trial.case_no}.json").write_text(
                        json.dumps(verdict), encoding="utf-8")
                    event = verdict
                emit(event)
            emit({"type": "left", "trials_left": self.court.trials_left(visitor)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            record = {"case_no": trial.case_no, "mode": "demo" if self.court.mock else "live",
                      **log.dump()}
            (DATA / "records" / f"{trial.case_no}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="smallclaims")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--port", type=int, default=8812)
    ap.add_argument("--daily-trials", type=int, default=3)
    ap.add_argument("--fee-cap", type=float, default=0.05)
    ap.add_argument("--pace", type=float, default=0.7, help="demo mode: seconds per event")
    ap.add_argument("--used-up", default="", help="visitors who start with no filings left, e.g. alice")
    args = ap.parse_args(argv)
    Handler.court = Court(args.mock, args.daily_trials, args.fee_cap, args.pace)
    for v in filter(None, args.used_up.split(",")):
        Handler.court.filed[(clean_visitor(v), date.today().isoformat())] = args.daily_trials
    mode = "DEMO (scripted, no key)" if Handler.court.mock else "LIVE on otari.ai"
    print(f"Small Claims in session at http://localhost:{args.port}  [{mode}]")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
