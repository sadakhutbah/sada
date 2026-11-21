"""Helpers for the end-to-end trials against the WSL preprod (console, relay, mail catcher, Studio application).

Run with the project's interpreter (httpx and websockets are dependencies):

    .venv/bin/python deploy/preprod/e2e/<scenario>.py

Local test values only: see docs/preprod-wsl.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

BASE = os.environ.get("PREPROD_URL", "http://127.0.0.1:8090")
MAIL = os.environ.get("PREPROD_MAIL_URL", "http://127.0.0.1:8025")
PASSWORD = os.environ.get("PREPROD_PASSWORD", "Preprod-Sada-2026")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(Path(__file__).resolve().parent.parent / "compose.yaml"),
    "--env-file",
    str(ENV_FILE),
]


def env_value(key: str) -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


class Console:
    """The back office as a signed-in person sees it (session cookie + anti-replay header)."""

    def __init__(self, username: str = "preprod-admin", password: str = PASSWORD):
        self.http = httpx.Client(base_url=BASE + "/api/v1", timeout=15)
        pre = self.http.get("/login-challenge").json()["pre"]
        r = self.http.post("/login", data={"username": username, "password": password, "pre": pre})
        r.raise_for_status()
        self.csrf = r.json()["csrf"]
        mosques = self.http.get("/mosques").json()["mosques"]  # hosted mode: no mosque opens by itself at sign-in
        r = self.http.post("/mosques/select", json={"mosque_id": mosques[0]["id"]}, headers={"X-CSRF": self.csrf})
        r.raise_for_status()

    def get(self, path: str, **kw) -> httpx.Response:
        return self.http.get(path, **kw)

    def post(self, path: str, body: dict | None = None, method: str = "POST") -> httpx.Response:
        return self.http.request(method, path, json=body or {}, headers={"X-CSRF": self.csrf})

    def devices(self) -> dict:
        return self.get("/studio/devices").json()

    def pair(self, user_code: str, name: str = "") -> httpx.Response:
        return self.post("/studio/devices/pair", {"user_code": user_code, "name": name})


def relay_path(slug: str = "preprod") -> dict | None:
    """What the relay says about live/<slug> (asked from inside the back office container: its API is private)."""
    code = f"import json;from sada.backoffice import live_watch as w;print(json.dumps(w.publisher_info('live/{slug}')))"
    out = subprocess.run(
        [*COMPOSE, "exec", "-T", "backoffice", "python", "-c", code], capture_output=True, text=True, timeout=30
    )
    return json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else None


def relay_get(path: str) -> dict:
    """GET on the relay's own API with the back office's credentials (inside the container)."""
    code = (
        "import json,httpx;from sada.backoffice import live_watch as w;"
        f"r=httpx.get('http://receiver:9997{path}',auth=(w.API_USER,w.api_password()),timeout=5);"
        "print(json.dumps({'status':r.status_code,'body':r.json() if r.content else None}))"
    )
    out = subprocess.run(
        [*COMPOSE, "exec", "-T", "backoffice", "python", "-c", code], capture_output=True, text=True, timeout=30
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def destination_paths() -> dict:
    """What the fake destination (stands for YouTube) receives."""
    return httpx.get("http://127.0.0.1:9998/v3/paths/list", timeout=5).json()


def mails() -> list[dict]:
    r = httpx.get(MAIL + "/api/v1/messages", timeout=5).json()
    return r.get("messages", [])


def clear_mails() -> None:
    httpx.delete(MAIL + "/api/v1/messages", timeout=5)


def wait_for(what: str, fn, timeout: float = 90, every: float = 2.0):
    """Polls `fn` until it returns something truthy; prints the wait; raises with `what` on timeout."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise TimeoutError(f"{what} (last: {last!r})")


def orchestrator_log(tail: int = 60) -> str:
    return subprocess.run(
        ["docker", "logs", "--tail", str(tail), "sada-preprod-orchestrator-1"], capture_output=True, text=True
    ).stdout


def sql(query: str) -> str:
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "sada", "-d", "sada", "-At", "-c", query],
        capture_output=True,
        text=True,
    ).stdout.strip()


class App:
    """The Studio application (Electron) driven through the Chromium DevTools protocol, under Xvfb."""

    def __init__(self, port: int = 9333):
        self.port = port

    async def _targets(self) -> list[dict]:
        async with httpx.AsyncClient() as c:
            return (await c.get(f"http://127.0.0.1:{self.port}/json")).json()

    async def _eval(self, expression: str, await_promise: bool = True):
        import websockets

        pages = [t for t in await self._targets() if t.get("type") == "page"]
        async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "awaitPromise": await_promise, "returnByValue": True},
                    }
                )
            )
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == 1:
                    return m.get("result", {}).get("result", {}).get("value")

    def eval(self, expression: str):
        return asyncio.run(self._eval(expression))

    def text(self) -> str:
        return self.eval("document.body.innerText") or ""

    def click(self, label: str) -> bool:
        js = (
            "(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim().toLowerCase().includes("
            + json.dumps(label.lower())
            + "));if(!b)return false;b.click();return true})()"
        )
        return bool(self.eval(js))

    def screenshot(self, path: str) -> None:
        async def go():
            import base64

            import websockets

            pages = [t for t in await self._targets() if t.get("type") == "page"]
            async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == 2:
                        Path(path).write_bytes(base64.b64decode(m["result"]["data"]))
                        return

        asyncio.run(go())
