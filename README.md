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
.venv/bin/python -m minbar.backoffice create-admin --username admin --mosque exemple --mosque-name "Mosquée Exemple"

# 2. Lancer : http://<adresse-du-pc>:8090 (téléphones sur le même réseau)
make backoffice        # = .venv/bin/python -m minbar.backoffice serve --port 8090
```

Les autres comptes se créent dans **Réglages → Comptes**, ou en ligne de commande :
`python -m minbar.backoffice create-user --username imam.abdallah --role imam --name "Imam Abdallah"`.

### Configurer la mosquée

Dans **Réglages → Mosquée** (ou `python -m minbar.backoffice mosque --slug exemple --orchestrator-url ...`) :

- **Orchestrateur** : adresse de l'API du live (défaut `http://127.0.0.1:8088`) et jeton éventuel. Les boutons
  +5 min / Janaza / Arrêter / Annuler aujourd'hui du tableau de bord passent par cette API.
- **Dossiers** : `data/state` (runs), `data/output` (transcriptions, clips) et `data/published` par défaut.
- **Import automatique** : les nouveaux runs du pipeline sont importés toutes les 60 s (`serve --import-every 0`
  pour désactiver ; bouton « Importer » dans Khutbas, ou `python -m minbar.backoffice import [--run 20261106_220224]`).
  Les sessions Janaza ne sont pas importées. L'import ne touche jamais aux validations ni aux corrections.
- **Imam par défaut** des nouvelles khutbas (sinon rattachement à la main sur la page de la khutba).
- **Connecteurs** : seul « Dossier local » publie en V1 (copie du clip + fiche JSON dans `data/published/<mosquée>/<date>/`).
  Facebook, YouTube et Instagram sont présents mais désactivés : aucun appel externe.

### Règles

- Rien ne se publie sans validation humaine (vérifié côté serveur, chaque tentative est journalisée).
- Corriger un sous-titre ou une traduction met les clips concernés en « à régénérer » : la régénération (ffmpeg,
  ~1 min) produit une nouvelle version, à revalider. Les versets gardent la traduction Hamidullah. Les fichiers du
  pipeline ne sont jamais modifiés : les corrections vivent dans la base, les clips régénérés sont de nouveaux fichiers.
- Journal : connexions, commandes du live, corrections, validations, publications, réglages (qui, quand, quoi).
- Base : `data/backoffice/minbar.db` (variable `MINBAR_DB` ou `--db`). Mots de passe argon2id, sessions serveur
  (cookie HttpOnly, SameSite), jeton CSRF, blocage après 5 essais ratés. Derrière HTTPS (serveur central) :
  `serve --secure-cookies`.
