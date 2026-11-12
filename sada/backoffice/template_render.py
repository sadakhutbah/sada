"""Render the imported screen template to a 1280×720 image, via a headless browser.

The mosque's template is HTML: the browser and the live screen must show the same thing, so the
image that goes on air is produced by a browser, not redrawn by hand. One Chromium is kept alive
for the whole broadcast; each screen is one screenshot.

No new dependency: the browser is driven over the Chrome DevTools Protocol through the
``websockets`` package, on an event loop living in its own thread. The engine keeps calling plain
blocking methods.

The template is a document, never a program: scripts are disabled in the page before the document
is inserted, so a file cannot run anything on the mosque's machine. Fonts are inlined, because a
file references ``../assets/fonts/<name>.ttf`` and this page has no URL to resolve it against.
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from ..i18n import _

FONTS = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

# The browser is shipped with the application, not borrowed from the machine: Playwright downloads
# its own build for the same reason. A mosque's server installs the package and nothing else.
EMBEDDED = Path(__file__).resolve().parent.parent.parent / "vendor" / "chromium"
ENGINE = "chrome-headless-shell"
WIDTH, HEIGHT = 1280, 720

# `url("../assets/fonts/X.ttf")` in the template, and the same path quoted or bare.
_FONT_REF = re.compile(r"""url\(\s*['"]?(?:\.\./)?assets/fonts/([A-Za-z0-9_.-]+\.ttf)['"]?\s*\)""")

# Names of the embedded engine, by platform: the shipped directory differs on Windows.
EMBEDDED_NAMES = ("chrome-headless-shell-linux64/chrome-headless-shell",
                  "chrome-headless-shell-win64/chrome-headless-shell.exe",
                  "chrome-headless-shell-mac-x64/chrome-headless-shell",
                  "chrome-headless-shell-mac-arm64/chrome-headless-shell")

# Last resort: a browser already installed on the machine.
CHROMIUM_CANDIDATES = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome")


class RenderUnavailable(RuntimeError):
    """No browser could be started: the caller falls back to the engine's own drawing."""


def inline_fonts(page: str) -> str:
    """Every font the template asks for, embedded in the document itself.

    The page is inserted without a URL, so a relative font path would resolve to nothing and the
    browser would fall back to the system fonts — that is exactly how a preview stops looking like
    the real screen.
    """
    cache: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in cache:
            path = FONTS / name
            if not path.is_file():
                return match.group(0)
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            cache[name] = f"url(data:font/ttf;base64,{data})"
        return cache[name]

    return _FONT_REF.sub(replace, page)


def embedded_path() -> Path | None:
    """The engine shipped with the application, if it was downloaded (see ``fetch_engine``)."""
    for name in EMBEDDED_NAMES:
        candidate = EMBEDDED / name
        if candidate.is_file():
            return candidate
    return None


def browser_path() -> str:
    shipped = embedded_path()
    if shipped is not None:
        return str(shipped)
    for candidate in CHROMIUM_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise RenderUnavailable(
        _("no rendering browser: run `python -m sada.backoffice fetch-engine` "
          "to download the one shipped with the application"))


ENGINE_VERSION_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
                      "last-known-good-versions-with-downloads.json")


def _platform() -> str:
    """The platform name used by the Chrome for Testing downloads."""
    import platform
    import sys

    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def fetch_engine(version: str | None = None, keep_zip: bool = False) -> Path:
    """Downloads the rendering engine shipped with the application, into ``vendor/chromium``.

    Written for an offline parish computer: one command, no package manager, no administrator
    rights, and the same image as the administrator's browser. The archive is about 130 MB and
    unpacks to about 280 MB — this is what is traded for not depending on the machine.
    """
    import io
    import urllib.request
    import zipfile

    with urllib.request.urlopen(ENGINE_VERSION_URL, timeout=60) as r:   # noqa: S310 — fixed host
        known = json.load(r)
    if version is None:
        version = known["channels"]["Stable"]["version"]
    plateforme = _platform()
    url = ""
    for item in known["channels"]["Stable"]["downloads"]["chrome-headless-shell"]:
        if item["platform"] == plateforme:
            url = item["url"]
    if not url:
        raise RenderUnavailable(_("no package for platform {platform}", platform=plateforme))
    print(_("Render: downloading {engine} {version} ({platform})…",
            engine=ENGINE, version=version, platform=plateforme))
    with urllib.request.urlopen(url, timeout=600) as r:                 # noqa: S310 — fixed host
        blob = r.read()
    cible = EMBEDDED
    if cible.exists():
        import shutil
        shutil.rmtree(cible)                    # a previous download is replaced, not merged
    cible.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(cible)
    for name in EMBEDDED_NAMES:                 # the executable bit does not survive a zip
        candidate = cible / name
        if candidate.is_file():
            candidate.chmod(0o755)
            print(_("Render: browser installed ({folder}, {version})",
                    folder=candidate.parent.name, version=version))
            return candidate
    raise RenderUnavailable(_("downloaded archive has no executable"))


class _Cdp:
    """Minimal CDP client: one page target, JSON commands over one WebSocket."""

    def __init__(self, url: str, loop: asyncio.AbstractEventLoop):
        self._url = url
        self._loop = loop
        self._ws = None
        self._ids = itertools.count(1)
        self._session: str | None = None
        self._stage: Path | None = None

    async def connect(self) -> None:
        import websockets

        self._ws = await websockets.connect(self._url, max_size=64 * 1024 * 1024)

    async def call(self, method: str, params: dict | None = None, session: bool = True) -> dict:
        msg: dict = {"id": next(self._ids), "method": method, "params": params or {}}
        if session and self._session:
            msg["sessionId"] = self._session
        await self._ws.send(json.dumps(msg))
        while True:
            raw = await self._ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg["id"]:
                if "error" in data:
                    raise RenderUnavailable(_("{method}: {error}", method=method, error=data["error"].get("message")))
                return data.get("result") or {}
            # events are ignored: this client only ever waits on its own replies

    async def open_page(self) -> None:
        result = await self.call("Target.createTarget", {"url": "about:blank"}, session=False)
        target = result["targetId"]
        attached = await self.call("Target.attachToTarget", {"targetId": target, "flatten": True},
                                   session=False)
        self._session = attached["sessionId"]
        await self.call("Page.enable")
        await self.call("Emulation.setDeviceMetricsOverride",
                        {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False})
        # a template is a document, never a program
        await self.call("Emulation.setScriptExecutionDisabled", {"value": True})

    async def show(self, page: str) -> None:
        """Puts the document in the page and waits until layout and fonts have settled.

        The file is served from disk rather than injected: a relative font path then resolves on its
        own, the browser caches the fonts between screens, and a 1280×720 screenshot lands well under
        the budget instead of re-parsing a megabyte of embedded base64 every second.
        """
        self._stage.write_text(page, encoding="utf-8")
        await self.call("Page.navigate", {"url": "file://" + str(self._stage)})
        # fonts are embedded, but layout still settles: wait for the document to say it is ready
        for _attempt in range(20):
            ready = await self.call("Runtime.evaluate", {
                "expression": "document.readyState === 'complete' && document.fonts.status === 'loaded'",
                "returnByValue": True})
            if (ready.get("result") or {}).get("value") is True:
                return
            await asyncio.sleep(0.02)

    async def screenshot(self) -> bytes:
        data = await self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        return base64.b64decode(data["data"])

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


def _stage_fonts(root: Path) -> Path:
    """Puts the template's fonts where a relative ``../assets/fonts/…`` resolves.

    A symbolic link is the cheap way, but Windows only grants one to an administrator or in
    developer mode: the copy is the portable answer (fonts are a handful of files, and they are
    written once per browser, not per screen).
    """
    assets = root / "assets"
    fonts = assets / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for source in sorted(FONTS.glob("*.ttf")):
        target = fonts / source.name
        if not target.exists():
            try:
                target.symlink_to(source)
            except OSError:
                import shutil
                shutil.copyfile(source, target)
    return assets


class TemplateRender:
    """One browser, many screenshots. ``image(page, variables)`` returns PNG bytes."""

    def __init__(self, timeout: float = 10.0, browser: str | None = None):
        self.timeout = timeout
        self._browser = browser
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cdp: _Cdp | None = None
        self._proc: subprocess.Popen | None = None
        self._profile: tempfile.TemporaryDirectory | None = None
        self._ready = threading.Event()
        self._failure: Exception | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="template-render", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout):
            raise RenderUnavailable(_("rendering browser: startup too slow"))
        if self._failure is not None:
            raise RenderUnavailable(str(self._failure))

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._open())
            self._ready.set()
            self._loop.run_forever()
        except Exception as e:                     # noqa: BLE001 — reported to the caller
            self._failure = e
            self._ready.set()

    async def _open(self) -> None:
        self._profile = tempfile.TemporaryDirectory(prefix="sada-render-")
        # `../assets/fonts/<name>.ttf` in the template must resolve on disk: the page is staged one
        # level below a directory that holds the engine's assets.
        root = Path(self._profile.name) / "site"
        site = root / "screens"
        site.mkdir(parents=True)
        self._assets = _stage_fonts(root)
        self._stage = site / "index.html"
        self._stage.write_text("", encoding="utf-8")
        # `file://` takes forward slashes on every system, Windows included; the page itself is
        # written by CDP as UTF-8, so the template's encoding is not left to the platform default.
        url = self._stage.resolve().as_uri()
        proc = subprocess.Popen(
            [self._browser or browser_path(), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--disable-extensions", "--mute-audio", "--hide-scrollbars",
             f"--window-size={WIDTH},{HEIGHT}", "--remote-debugging-port=0",
             f"--user-data-dir={self._profile.name}", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        self._proc = proc
        url = None
        for line in proc.stderr:                   # type: ignore[union-attr]
            match = re.search(r"ws://\S+", line or "")
            if match:
                url = match.group(0)
                break
        if not url:
            raise RenderUnavailable(_("browser: debugging address not found"))
        self._cdp = _Cdp(url, self._loop)          # type: ignore[arg-type]
        self._cdp._stage = self._stage
        await self._cdp.connect()
        await self._cdp.open_page()

    def close(self) -> None:
        if self._loop is not None and self._cdp is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._cdp.close(), self._loop).result(3)
            except Exception:                      # noqa: BLE001 — on ferme, coûte que coûte
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._profile is not None:
            self._profile.cleanup()
        self._thread = self._loop = self._cdp = self._proc = self._profile = None

    # --------------------------------------------------------------------- render
    def image(self, page: str, variables: dict | None = None) -> bytes:
        """PNG of one screen. Raises ``RenderUnavailable`` if the browser is gone."""
        if self._loop is None or self._cdp is None:
            raise RenderUnavailable(_("rendering browser not started"))
        if variables:
            for name, value in variables.items():
                page = page.replace("{" + name + "}", value or "")
        with self._lock:                           # one screenshot at a time, one page
            async def run() -> bytes:
                await self._cdp.show(page)         # type: ignore[union-attr]
                return await self._cdp.screenshot()  # type: ignore[union-attr]

            future = asyncio.run_coroutine_threadsafe(run(), self._loop)
            try:
                return future.result(self.timeout)
            except Exception as e:                 # noqa: BLE001 — the caller decides to fall back
                raise RenderUnavailable(_("screen rendering impossible ({error})", error=e)) from e
