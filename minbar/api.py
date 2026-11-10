"""Local HTTP API for manual control (stdlib).  Listens on 127.0.0.1 by default; optional X-Token.

GET  /status
POST /<command>?name=...|min=...    Machine.COMMANDS commands (e.g. /extend?min=5, /scene?name=priere,
                                    /janaza?name=...[&info=...]: name and note of the deceased, only with the family's consent)
GET  /  : small control page (/?token=... if a token is configured)
"""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .scenes import SCENE_LABELS
from .state import STATE_OF_SCENE, Machine

# parameterised commands: one button per value (the janaza scene goes through the janaza command, which opens its session)
_VARIANTS = {
    "extend": [["extend?min=5", "+5 min"], ["extend?min=15", "+15 min"]],
    "scene": [[f"scene?name={sc}", SCENE_LABELS[sc]]
              for sc in STATE_OF_SCENE if sc != "janaza"],
}
_BUTTONS = [b for c in Machine.COMMANDS for b in _VARIANTS.get(c, [[c]])]

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Minbar Live</title><body style="font-family:sans-serif;background:#0f3d20;color:#fff;max-width:480px;margin:auto">
<h2 style="color:#8DC63F">Minbar Live — contrôle</h2><pre id=s style="background:#1E7A3C;padding:8px;white-space:pre-wrap"></pre>
<div id=b></div><script>
const cmds=""" + json.dumps(_BUTTONS) + """;
b.innerHTML=cmds.map(c=>`<button style="font-size:18px;margin:4px;padding:8px" onclick="go('${c[0]}')">${c[1]||c[0]}</button>`).join("");
const H={"X-Token":new URLSearchParams(location.search).get("token")||""};
async function go(p){await fetch("/"+p,{method:"POST",headers:H});ref()}
async function ref(){const r=await(await fetch("/status",{headers:H})).json();s.textContent=JSON.stringify(r,null,1)}
ref();setInterval(ref,2000)</script>"""


def make_server(orch, host: str, port: int, token: str = "") -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj, ctype="application/json"):
            body = (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            if token and self.headers.get("X-Token") != token:
                self._send(401, {"error": "jeton requis"})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(200, PAGE, "text/html")
            if not self._auth():
                return
            if u.path == "/status":
                return self._send(200, orch.status())
            self._send(404, {"error": "inconnu"})

        def do_POST(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            cmd = u.path.strip("/")
            arg = q.get("name") or q.get("min")
            if q.get("info"):                       # janaza record: "name|note"
                arg = f'{q.get("name", "")}|{q["info"]}'
            if cmd not in Machine.COMMANDS:
                return self._send(404, {"error": "commande inconnue"})
            self._send(200, {"ok": True, **orch.manual(cmd, arg)})

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="api").start()
    return srv
