"""Orchestrator configuration: JSON file, environment, command line.

ONE precedence rule for every setting, applied by `Config.load`:
command line > MINBAR_<FIELD> environment > --config file > default. A setting counts as given
when it is neither None nor an empty string, so an unset flag never erases the file.

The state-machine settings (schedule, signals) are those of MachineParams."""
from __future__ import annotations
import json
import os
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path

from . import defaults, paths
from .state import MachineParams, SessionParams

ENV_PREFIX = "MINBAR_"


def pick(*candidates):
    """First value that is set (neither None nor ""), last candidate as default: the one rule."""
    for c in candidates[:-1]:
        if c is not None and c != "":
            return c
    return candidates[-1] if candidates else None


def env_name(name: str) -> str:
    """Environment variable of a setting: MINBAR_<FIELD>."""
    return ENV_PREFIX + name.upper()


def convert(raw, template):
    """Value read as text (environment, command line) turned into the type of the default."""
    if isinstance(template, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on", "oui")
    if isinstance(template, int):
        return int(raw)
    if isinstance(template, float):
        return float(raw)
    if isinstance(template, (list, tuple)):
        return [x.strip() for x in str(raw).split(",") if x.strip()]
    return str(raw)


@dataclass
class Config(MachineParams):
    # --- Inputs / outputs ---------------------------------------------------
    camera_url: str = "rtsp://127.0.0.1:8554/cam"
    # RTMP output: in demo mode, a local server playing Facebook's role.
    # In production: rtmps://live-api-s.facebook.com:443/rtmp/<key> (key read from the MINBAR_RTMP_URL env).
    rtmp_url: str = "rtmp://127.0.0.1:1935/live/facebook"
    record_dir: str = str(paths.RECORDINGS)
    state_dir: str = str(paths.STATE)
    width: int = 1280
    height: int = 720
    fps: int = 25
    video_bitrate: str = "2500k"
    # Clean recording (camera without overlay) for clips: live_*_clean.mkv, in the same ffmpeg.
    record_clean: bool = True
    clean_copy: bool = True             # camera stream copy (original quality); False = x264 crf 18, 1280x720
    # --- Schedule -----------------------------------------------------------
    # Leave empty for the mosque: configure the Mawaqit URL (cli --mawaqit-url / Settings → Mosque)
    # or impose the times (cli --times, jumua_times_override field).
    mawaqit_url: str = ""
    mawaqit_cache: str = str(paths.CACHE / "mawaqit.json")
    jumua_times_override: list[str] = field(default_factory=list)  # e.g. ["12:30", "13:30"]
    events_file: str = str(paths.EVENTS)   # programme: dars, conferences
    # --- Simulated clock ----------------------------------------------------
    sim_start: str | None = None        # "HH:MM": simulated start time (None = real time)
    sim_speed: float = 1.0              # acceleration outside the khutba
    # --- Signals ------------------------------------------------------------
    vad_aggressiveness: int = 3
    vad_ratio: float = 0.5              # share of voiced frames over 1 s to call it "voice"
    # --- Overlay (mosque identity, to be defined for each mosque) ------------
    mosque_name: str = ""               # e.g. "Mosquée Assalam" (footer, khutba banner, API)
    mosque_name_ar: str = ""            # e.g. "مسجد السلام" (empty: only the Latin name is shown)
    logo: str = ""                      # path of the mosque logo (jpg/png); empty = neutral emblem drawn
    # --- Presence at the minbar (v1: motion, without YOLO) ------------------
    presence: str = "off"               # off | motion
    presence_threshold: float = 0.004   # share of pixels in the zone that change to call it "motion"
    presence_roi: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])  # x0,y0,x1,y1 as fractions
    presence_hold_s: float = 8.0        # a motion counts as "present" for this delay
    presence_pixel_delta: int = 12      # level difference (0-255) counted as a real change
    presence_fps: float = 2.0           # analysed frames per second (2 is enough, negligible cost)
    presence_width: int = 160
    presence_height: int = 90
    # --- Face at the minbar (YuNet, CPU): end-of-khutba signal ----------------
    face: str = "yunet"                 # off | yunet
    face_threshold: float = 0.6         # minimum score for a frontal face
    face_hold_s: float = 4.0            # a face seen counts as "present" for this delay
    face_roi: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])  # x0,y0,x1,y1 as fractions
    face_fps: float = 5.0               # analysed frames per second
    face_width: int = 320
    face_height: int = 180
    title: str = defaults.CLIP_TITLE
    dark_green: str = "#1E7A3C"
    # --- Notifications (webhook or Telegram; nothing published) --------------
    notify_url: str = ""                # generic webhook (Discord, Slack, n8n…) that receives {"text": ...}
    telegram_token: str = ""            # Telegram bot: token and conversation, to be kept out of the repository
    telegram_chat: str = ""
    # --- API ----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8088
    api_token: str = ""                 # if set: X-Token header required
    # --- Back-office (same machine) ------------------------------------------
    # Where to ask for the settings an administrator saved in the interface (notifications).
    backoffice_url: str = "http://127.0.0.1:8090"

    @classmethod
    def load(cls, path: str | None = None, **overrides) -> "Config":
        """Effective configuration, one setting at a time: flag > environment > file > default."""
        data = json.loads(Path(path).read_text()) if path else {}
        vals = {}
        for f in fields(cls):
            template = f.default if f.default is not MISSING else (
                f.default_factory() if f.default_factory is not MISSING else None)
            raw = pick(overrides.get(f.name), os.environ.get(env_name(f.name)), data.get(f.name), None)
            if raw is None:
                continue
            if isinstance(template, SessionParams):       # nested section: {"khutba": {...}}
                vals[f.name] = raw if isinstance(raw, SessionParams) else replace(template, **raw)
            elif isinstance(raw, str):
                vals[f.name] = convert(raw, template)
            else:
                vals[f.name] = raw
        return cls(**vals)
