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
    # Laissez vide pour la mosquée : configurez l'URL Mawaqit (cli --mawaqit-url / Réglages → Mosquée)
    # ou imposez les horaires (cli --times, champ jumua_times_override).
    mawaqit_url: str = ""
    mawaqit_cache: str = str(paths.CACHE / "mawaqit.json")
    jumua_times_override: list[str] = field(default_factory=list)  # ex. ["12:30", "13:30"]
    events_file: str = str(paths.DATA / "events.json")   # programme : dars, conférences
    # --- Horloge simulée ----------------------------------------------------
    sim_start: str | None = None        # "HH:MM" : heure simulée de départ (None = heure réelle)
    sim_speed: float = 1.0              # accélération hors khutba
    # --- Signaux ------------------------------------------------------------
    vad_aggressiveness: int = 3
    vad_ratio: float = 0.5              # part de trames voisées sur 1 s pour dire « voix »
    # --- Habillage (identité de la mosquée, à définir pour chaque mosquée) ---
    mosque_name: str = ""               # ex. "Mosquée Assalam" (pied de page, bandeau khutba, API)
    mosque_name_ar: str = ""            # ex. "مسجد السلام" (vide : seul le nom latin est affiché)
    logo: str = ""                      # chemin du logo de la mosquée (jpg/png) ; vide = emblème neutre dessiné
    # --- Présence au minbar (v1 : mouvement, sans YOLO) ---------------------
    presence: str = "off"               # off | motion
    presence_threshold: float = 0.004   # part de pixels de la zone qui changent pour dire « mouvement »
    presence_roi: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])  # x0,y0,x1,y1 en fractions
    presence_hold_s: float = 8.0        # un mouvement vaut « présent » pendant ce délai
    presence_pixel_delta: int = 12      # écart de niveau (0-255) compté comme un changement réel
    presence_fps: float = 2.0           # images analysées par seconde (2 suffit, coût négligeable)
    presence_width: int = 160
    presence_height: int = 90
    terminated_hold_s: float = 45.0     # écran Terminé avant arrêt du stream
    title: str = "Khutba du vendredi"
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
