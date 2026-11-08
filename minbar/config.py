"""Configuration de l'orchestrateur (surchargeable par un fichier JSON via --config).

Les réglages de la machine à états (horaires, signaux) sont ceux de MachineParams."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .state import MachineParams


@dataclass
class Config(MachineParams):
    # --- Entrées / sorties --------------------------------------------------
    camera_url: str = "rtsp://127.0.0.1:8554/cam"
    # Sortie RTMP : en démo, serveur local qui joue le rôle de Facebook.
    # En production : rtmps://live-api-s.facebook.com:443/rtmp/<clé> (clé lue dans l'env MINBAR_RTMP_URL).
    rtmp_url: str = "rtmp://127.0.0.1:1935/live/facebook"
    record_dir: str = str(paths.RECORDINGS)
    state_dir: str = str(paths.STATE)
    width: int = 1280
    height: int = 720
    fps: int = 25
    video_bitrate: str = "2500k"
    # Enregistrement propre (caméra sans habillage) pour les clips : live_*_clean.mkv, dans le même ffmpeg.
    record_clean: bool = True
    clean_copy: bool = True             # copie du flux caméra (qualité d'origine) ; False = x264 crf 18 en 1280x720
    # --- Horaires -----------------------------------------------------------
    mawaqit_url: str = "https://mawaqit.net/fr/m/mosquee-exemple"
    mawaqit_cache: str = str(paths.CACHE / "mawaqit.json")
    jumua_times_override: list[str] = field(default_factory=list)  # ex. ["12:30", "13:30"]
    # --- Horloge simulée ----------------------------------------------------
    sim_start: str | None = None        # "HH:MM" : heure simulée de départ (None = heure réelle)
    sim_speed: float = 1.0              # accélération hors khutba
    # --- Signaux ------------------------------------------------------------
    vad_aggressiveness: int = 3
    vad_ratio: float = 0.5              # part de trames voisées sur 1 s pour dire « voix »
    # --- Habillage ----------------------------------------------------------
    mosque_name: str = "Mosquée Exemple"
    mosque_name_ar: str = "مسجد بونطواز"
    title: str = "Khutba du vendredi"
    logo: str = str(paths.ASSETS / "logo-fb.jpg")
    dark_green: str = "#1E7A3C"
    # --- API ----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8088
    api_token: str = ""                 # si défini : en-tête X-Token requis

    @classmethod
    def load(cls, path: str | None = None, **overrides) -> "Config":
        data = json.loads(Path(path).read_text()) if path else {}
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)
