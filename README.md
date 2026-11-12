<p align="center">
  <img src="web/public/sada-mark-or.svg" alt="Sadā" width="96">
</p>

<h1 align="center">Sadā · صدى</h1>

<p align="center">
  Automatic live streaming of the Friday khutba, and an AI pipeline that turns it into transcripts,
  translations and short clips.
</p>

<p align="center">
  <a href="https://github.com/sadakhutbah/sada/actions/workflows/ci.yml"><img src="https://github.com/sadakhutbah/sada/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-E2C27A" alt="License: AGPL-3.0"></a>
</p>

<p align="center">
  <b>English</b> · <a href="README.ar.md">العربية</a>
</p>

---

Sadā runs on a small computer at the mosque. It watches the prayer times, starts the live stream on its
own when the imam steps up to the minbar, stops when the prayer begins, and sends the same stream to
Facebook, YouTube or any RTMP destination. After the live, it transcribes the khutba (Arabic and French),
detects the Qur'an verses quoted, translates them, and prepares short clips that a human validates
before anything is published.

Nobody needs to stay behind the camera.

## Features

- **Hands-free live**: prayer times from Mawaqit, start on voice + presence at the minbar, end of the
  khutba detected when the imam turns towards the qibla. Manual controls stay available
  (+5 min, Janaza, stop, cancel today).
- **Multi-destination streaming**: one encoder, several RTMP outputs; a platform that drops does not
  stop the others.
- **Full-screen slates**: before the live, prayer, intermission, end, Janaza, end of a lesson, with
  texts editable per mosque.
- **Lessons and lectures (dars)**: weekly or one-off events, scheduled at a fixed time or after a prayer.
- **Post-live pipeline**: transcription (faster-whisper), Qur'an verse detection, translation, summary,
  clips with burned-in subtitles. Works with any local or remote LLM (OpenAI or Anthropic compatible
  API), and falls back gracefully without one.
- **Back office**: mobile-friendly web app with roles (admin, manager, imam), human validation before
  every publication, full audit log.
- **Publishing**: Facebook page, YouTube, Instagram Reels, or a local folder. Optional upload of
  recordings to S3 / Cloudflare R2.
- **Privacy first**: the camera frames the minbar only; presence and face signals are computed on the
  fly, no image or identity is ever stored.

## Two ways to use it

- **Self-hosted**: install it on the mosque's computer (this repository).
- **Hosted**: the mosque signs in to the hosted version and streams from OBS, nothing to install.

## Quick start

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), `ffmpeg`, and the
[mediamtx](https://github.com/bluenviron/mediamtx) relay for live streaming
(both can be dropped in `tools/bin/`).

```bash
make setup                 # virtualenv + dependencies
python -m sada.installer   # checks the machine and says what is missing

# create the mosque and its first administrator (password asked interactively)
.venv/bin/python -m sada.backoffice create-admin --username admin \
    --mosque my-mosque --mosque-name "My Mosque"

make backoffice            # http://<this-machine>:8090
```

The first administrator sign-in opens a four-step setup: video input, live destinations, recordings,
summary.

## Documentation

The administrator guides are served by the app itself under `/docs`, and live in [`docs/`](docs):

- [Getting started](docs/mise-en-route.en.md)
- [Orchestrator](docs/orchestrateur.en.md): the two processes, tokens, start order
- [Connectors](docs/connecteurs.en.md): Facebook, YouTube, Instagram
- [Recordings](docs/rediffusions.en.md): clips, published folder, S3 / R2
- [Screens](docs/ecrans-mode-emploi.en.md) and [screen template prompt](docs/agent-gabarit-ecrans.en.md)

## Development

```bash
make test     # test suite
make front    # rebuild the React front end (web/) into web/dist
```

Layout: `sada/` (orchestrator, encoder, signals), `sada/pipeline/` (post-live AI), `sada/backoffice/`
(FastAPI + SQLite), `web/` (React front end), `tests/`.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[GNU AGPL-3.0](LICENSE). You may use, modify and host Sadā; if you make a modified version available,
including as an online service, its source code must be published under the same license.
Bundled fonts keep their own license (SIL OFL, see `assets/fonts/`).
