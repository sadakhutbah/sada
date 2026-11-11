"""Local orchestrator API (sada.api): JSON only, commands and status."""
import json
import socket
import urllib.error
import urllib.request


class FakeOrch:
    def __init__(self):
        self.calls = []

    def manual(self, cmd, arg=None):
        self.calls.append((cmd, arg))
        return {"message": f"ok {cmd}", "state": "KHUTBA"}

    def status(self):
        return {"state": "PRAYER", "clock": "13:42:10", "stream_on": True}


def _serve(token=""):
    from sada.api import make_server
    orch = FakeOrch()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = make_server(orch, "127.0.0.1", port, token)
    return orch, srv, f"http://127.0.0.1:{port}"


def _get(url, token=""):
    req = urllib.request.Request(url, headers={"X-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def _post(url, token=""):
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={"X-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_status_est_du_json():
    orch, srv, base = _serve()
    try:
        code, ctype, body = _get(base + "/status")
        assert code == 200 and ctype.startswith("application/json")
        assert json.loads(body)["state"] == "PRAYER"
    finally:
        srv.shutdown()


def test_plus_de_page_html_de_controle():
    """The embedded control page is gone: the root answers JSON, not HTML."""
    orch, srv, base = _serve()
    try:
        code, ctype, body = _get(base + "/")
        assert code == 404 and ctype.startswith("application/json")
        assert "text/html" not in body.decode().lower()
    finally:
        srv.shutdown()


def test_commande_inconnue_refusee():
    orch, srv, base = _serve()
    try:
        code, body = _post(base + "/decoller")
        assert code == 404 and "error" in body and orch.calls == []
    finally:
        srv.shutdown()


def test_commande_parametree_transmise():
    orch, srv, base = _serve()
    try:
        code, body = _post(base + "/extend?min=15")
        assert code == 200 and body["ok"] is True
        assert orch.calls == [("extend", "15")]
    finally:
        srv.shutdown()


def test_jeton_exige_quand_configure():
    orch, srv, base = _serve(token="secret")
    try:
        assert _get(base + "/status")[0] == 401          # no page without the token, not even an HTML one
        assert _post(base + "/stop")[0] == 401
        assert orch.calls == []
        assert _get(base + "/status", token="secret")[0] == 200
    finally:
        srv.shutdown()
