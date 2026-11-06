#!/usr/bin/env bash
# Lance mediamtx (RTSP :8554 + RTMP :1935, local uniquement) depuis la racine du projet.
cd "$(dirname "$0")/.." && exec tools/bin/mediamtx tools/mediamtx.yml
