# Minbar Live

Diffusion automatique de la khutba et pipeline IA après le live (transcription, versets, traduction, clips). Spécification : `SPEC.md`.

```bash
make setup     # .venv + dépendances (uv)
make test      # tests automatisés
make demo      # démo complète (caméra simulée, orchestrateur accéléré)
```

## Back-office

Application web authentifiée (FastAPI + SQLite), utilisable sur téléphone. Elle tourne sur l'ordinateur de la mosquée
(mini-PC NVIDIA ou PC de la mosquée) et, dans le modèle de données, chaque élément appartient à une mosquée : la même
application pourra servir plusieurs mosquées depuis un serveur central.

| Rôle | Peut |
|---|---|
| Administrateur | tout, dont comptes, réglages de la mosquée et connecteurs |
| Responsable | live, historique complet, validation, publication, journal |
| Imam | live, ses khutbas, corriger/valider les clips de ses khutbas (pas de publication) |

### Lancer

```bash
# 1. Créer la mosquée et le premier administrateur (le mot de passe est demandé au clavier)
.venv/bin/python -m minbar.backoffice create-admin --username admin --mosque votre-mosquee --mosque-name "Votre mosquée"

# 2. Lancer : http://<adresse-du-pc>:8090 (téléphones sur le même réseau)
make backoffice        # = .venv/bin/python -m minbar.backoffice serve --port 8090
```

Les autres comptes se créent dans **Réglages → Comptes**, ou en ligne de commande :
`python -m minbar.backoffice create-user --username imam.abdallah --role imam --name "Imam Abdallah"`.

### Configurer la mosquée

Dans **Réglages → Mosquée** (ou `python -m minbar.backoffice mosque --slug votre-mosquee --orchestrator-url ...`) :

- **Orchestrateur** : adresse de l'API du live (défaut `http://127.0.0.1:8088`) et jeton éventuel. Les boutons
  +5 min / Janaza / Arrêter / Annuler aujourd'hui du tableau de bord passent par cette API.
- **Dossiers** : `data/state` (runs), `data/output` (transcriptions, clips) et `data/published` par défaut.
- **Import automatique** : les nouveaux runs du pipeline sont importés toutes les 60 s (`serve --import-every 0`
  pour désactiver ; bouton « Importer » dans Khutbas, ou `python -m minbar.backoffice import [--run 20261106_220224]`).
  Les sessions Janaza ne sont pas importées. L'import ne touche jamais aux validations ni aux corrections.
- **Imam par défaut** des nouvelles khutbas (sinon rattachement à la main sur la page de la khutba).
- **Connecteurs** : seul « Dossier local » publie en V1 (copie du clip + fiche JSON dans `data/published/<mosquée>/<date>/`).
  Facebook, YouTube et Instagram sont présents mais désactivés : aucun appel externe.

## Programme : dars et conférences

Outre les khutbas du vendredi (horaires Mawaqit), l'orchestrateur suit un programme de dars et de
conférences dans `data/events.json` (modèle : `examples/events.example.json`, à copier).

```json
{"kind": "dars", "title": "Tafsir de la sourate Al-Kahf", "speaker": "Imam Abdallah",
 "series": "Tafsir Al-Kahf", "session": 7,
 "recurrence": {"weekday": "mardi", "after": "isha", "offset_min": 15}}
```

- `date` + `time` pour un événement unique ; `recurrence` pour un cours hebdomadaire, à l'heure
  absolue (`time`) ou après une prière (`after` = dhuhr, asr, maghrib, isha + `offset_min`), recalculée
  chaque jour avec les horaires Mawaqit.
- `publish: false` garde une entrée de côté (brouillon, annonce non confirmée).
- Démarrage sur les mêmes signaux que la khutba, mais plus souple : fenêtre de 90 min (au lieu de 45),
  silence d'arrêt de 3 min, grâce de 90 s, durée maximale de 2 h.
- Écrans dédiés : bandeau « Dars : titre · intervenant · série · séance N » sur la caméra, compte à
  rebours « Prochain cours à … » entre deux sessions, écran de fin avec le prochain cours annoncé.
  L'API de contrôle expose `kind` et `event`, et les boutons `Dars` / `Fin dars`.
- Un fichier illisible n'empêche pas la diffusion : l'erreur est journalisée et les khutbas restent
  programmées.

## Modèle de langage (résumé et traduction)

Le pipeline utilise un modèle servi en local, par HTTP, au format **OpenAI** ou **Anthropic**. Aucun
binaire externe n'est requis : tout serveur compatible convient (Ollama, llama.cpp, LM Studio, vLLM, ou
une API distante). Sans configuration, l'adresse visée est Ollama sur la machine
(`http://127.0.0.1:11434/v1`) ; si rien ne répond, le pipeline continue avec ses replis — résumé
extractif et traductions fixes — et la note Markdown l'indique.

```bash
export MINBAR_LLM_URL="http://127.0.0.1:11434/v1"      # racine de l'API (…/v1, …/v1/messages)
export MINBAR_LLM_MODEL="qwen2.5:7b-instruct"          # nom du modèle côté serveur
export MINBAR_LLM_PROTOCOL="openai"                    # openai | anthropic (déduit de l'URL sinon)
export MINBAR_LLM_API_KEY="…"                          # seulement si le serveur en exige une
```

Équivalents en options : `--llm-url`, `--llm-model`, `--llm-protocol`. La méthode réellement employée
(`openai · qwen2.5:7b-instruct`) est écrite dans l'en-tête des notes. L'ancien appel au binaire
`claude`, en sous-processus, n'est plus le défaut : il faut le demander avec
`MINBAR_LLM_PROTOCOL=claude-cli`.

## Notifications à l'imam

L'orchestrateur prévient l'imam par un message court — rappel avant la session, début du direct,
fin du direct — vers une destination privée. Rien n'est publié, et sans destination configurée les
messages restent dans le journal (`notification (à blanc) : …`).

```bash
export MINBAR_NOTIFY_URL="https://discord.com/api/webhooks/…"   # webhook générique {"text": …}
# ou
export MINBAR_TELEGRAM_TOKEN="…" MINBAR_TELEGRAM_CHAT="…"        # bot Telegram
```

Le jeton Telegram et l'URL du webhook se gardent hors du dépôt (variables d'environnement ou
fichier de configuration local). Un échec d'envoi est journalisé et n'interrompt jamais le direct.
Les messages existent aussi en champs de configuration : `notify_url`, `telegram_token`, `telegram_chat`
(ou les options `--notify-url`, `--telegram-token`, `--telegram-chat`).

## Présence au minbar

Le démarrage automatique exige deux signaux : la voix (`vad`, webrtcvad) et, si elle est activée, la
**présence** devant le minbar. La v1 ne fait pas tourner YOLO : un ffmpeg lit la caméra à 2 i/s en
160×90 niveaux de gris, et l'on compare chaque image à la précédente dans une zone choisie. Aucune image
n'est écrite, aucun visage n'est calculé ; il ne sort qu'un score et un booléen.

```yaml
# config.yaml
presence: motion            # off | motion
presence_roi: [0.25, 0.10, 0.75, 0.95]   # zone du minbar, en fractions (x0, y0, x1, y1)
presence_threshold: 0.004   # part de pixels de la zone qui changent pour dire « mouvement »
presence_hold_s: 8          # un mouvement vaut « présent » pendant 8 s
presence_pixel_delta: 12    # écart de niveau (0–255) compté comme un vrai changement
```

Calibrage (une fois, depuis la caméra réelle) : lancer `python -m minbar.orchestrator --presence motion`,
puis `curl -s 127.0.0.1:8088/status` → `presence.score` au repos (0) et quand quelqu'un monte au minbar.
Le seuil se met à mi-chemin entre les deux. Si la caméra ne donne plus d'image, `presence.present` vaut
`null` : la machine ignore alors ce signal au lieu de bloquer le démarrage.

### Règles

- Rien ne se publie sans validation humaine (vérifié côté serveur, chaque tentative est journalisée).
- Corriger un sous-titre ou une traduction met les clips concernés en « à régénérer » : la régénération (ffmpeg,
  ~1 min) produit une nouvelle version, à revalider. Les versets gardent la traduction Hamidullah. Les fichiers du
  pipeline ne sont jamais modifiés : les corrections vivent dans la base, les clips régénérés sont de nouveaux fichiers.
- Journal : connexions, commandes du live, corrections, validations, publications, réglages (qui, quand, quoi).
- Base : `data/backoffice/minbar.db` (variable `MINBAR_DB` ou `--db`). Mots de passe argon2id, sessions serveur
  (cookie HttpOnly, SameSite), jeton CSRF, blocage après 5 essais ratés. Derrière HTTPS (serveur central) :
  `serve --secure-cookies`.
