"""Configuration par défaut (surchargeable par un fichier JSON via --config)."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    # --- Entrées / sorties --------------------------------------------------
    camera_url: str = "rtsp://127.0.0.1:8554/cam"
    # Sortie RTMP : en démo, serveur local qui joue le rôle de Facebook.
    # En production : rtmps://live-api-s.facebook.com:443/rtmp/<clé> (clé lue dans l'env MINBAR_RTMP_URL).
    rtmp_url: str = "rtmp://127.0.0.1:1935/live/facebook"
    record_dir: str = str(ROOT / "data" / "recordings")
    state_dir: str = str(ROOT / "data" / "state")
    width: int = 1280
    height: int = 720
    fps: int = 25
    video_bitrate: str = "2500k"
    # Enregistrement propre (caméra sans habillage) pour les clips : live_*_clean.mkv, dans le même ffmpeg.
    record_clean: bool = True
    clean_copy: bool = True             # copie du flux caméra (qualité d'origine) ; False = x264 crf 18 en 1280x720
    # --- Horaires -----------------------------------------------------------
    mawaqit_url: str = "https://mawaqit.net/fr/m/mosquee-exemple"
    mawaqit_cache: str = str(ROOT / "data" / "cache" / "mawaqit.json")
    jumua_times_override: list[str] = field(default_factory=list)  # ex. ["12:30", "13:30"]
    arm_before_min: float = 15.0        # armement T-15 min
    avant_before_min: float = 2.0       # scène Avant T-2 min (stream démarré)
    window_after_min: float = 45.0      # au-delà de T+45 min sans signal : session sautée
    prayer_min: float = 12.0            # durée supposée de la prière (min d'horloge)
    # --- Horloge simulée ----------------------------------------------------
    sim_start: str | None = None        # "HH:MM" : heure simulée de départ (None = heure réelle)
    sim_speed: float = 1.0              # accélération hors khutba
    # --- Signaux (durées en secondes RÉELLES : le signal est en temps réel) ---
    voice_start_s: float = 20.0         # présence + voix pendant 20 s
    silence_stop_s: float = 60.0        # silence prolongé => fin de khutba
    grace_s: float = 30.0               # délai de grâce avant de couper
    min_khutba_s: float = 120.0         # pas d'arrêt avant cette durée
    max_khutba_s: float = 3600.0        # garde-fou durée max
    vad_aggressiveness: int = 3
    vad_ratio: float = 0.5              # part de trames voisées sur 1 s pour dire « voix »
    presence: str = "off"               # off | motion (v1 : pas de YOLO, voir README)
    presence_threshold: float = 0.004
    terminated_hold_s: float = 45.0     # écran Terminé avant arrêt du stream
    # --- Habillage ----------------------------------------------------------
    mosque_name: str = "Mosquée Exemple"
    title: str = "Khutba du vendredi"
    logo: str = str(ROOT / "assets" / "logo-fb.jpg")
    green: str = "#8DC63F"
    dark_green: str = "#1E7A3C"
    # --- API ----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8088
    api_token: str = ""                 # si défini : en-tête X-Token requis

    @classmethod
    def load(cls, path: str | None = None, **overrides) -> "Config":
        data = {}
        if path:
            data = json.loads(Path(path).read_text())
        data.update({k: v for k, v in overrides.items() if v is not None})
        cfg = cls(**data)
        Path(cfg.record_dir).mkdir(parents=True, exist_ok=True)
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        return cfg

    def dump(self) -> dict:
        return asdict(self)
