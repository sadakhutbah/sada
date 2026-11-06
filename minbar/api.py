"""API HTTP locale de contrôle manuel (stdlib).  Écoute 127.0.0.1 par défaut ; jeton X-Token optionnel.

GET  /status
POST /start  /stop  /pause  /resume  /auto  /skip  /cancel
POST /scene?name=avant|khutba|priere|entracte|termine|janaza
POST /extend?min=5
POST /janaza?name=...       (nom du défunt optionnel, seulement avec l'accord de la famille)
GET  /  : petite page de contrôle
"""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Minbar Live</title><body style="font-family:sans-serif;background:#0f3d20;color:#fff;max-width:480px;margin:auto">
<h2 style="color:#8DC63F">Minbar Live — contrôle</h2><pre id=s style="background:#1E7A3C;padding:8px;white-space:pre-wrap"></pre>
<div id=b></div><script>
const cmds=[["start"],["pause"],["resume"],["stop"],["auto"],["skip"],["cancel"],["extend?min=5","+5 min"],["extend?min=15","+15 min"],
["scene?name=avant","Avant"],["scene?name=khutba","Khutba"],["scene?name=priere","Prière"],["scene?name=entracte","Entracte"],["scene?name=termine","Terminé"],["janaza","Janaza"]];
b.innerHTML=cmds.map(c=>`<button style="font-size:18px;margin:4px;padding:8px" onclick="go('${c[0]}')">${c[1]||c[0]}</button>`).join("");
async function go(p){await fetch("/"+p,{method:"POST"});ref()}
async function ref(){const r=await(await fetch("/status")).json();s.textContent=JSON.stringify(r,null,1)}
ref();setInterval(ref,2000)</script>"""


def make_server(orch, host: str, port: int, token: str = "") -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencieux
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
            if cmd not in ("start", "stop", "pause", "resume", "auto", "skip", "cancel", "scene", "extend", "janaza"):
                return self._send(404, {"error": "commande inconnue"})
            msg = orch.manual(cmd, arg)
            self._send(200, {"ok": True, "message": msg, "state": orch.machine.state.value})

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="api").start()
    return srv
