# Front React du back-office

Vite + React 19 + TypeScript + React Router 7 + `react-i18next` (français par défaut, anglais,
arabe prévu avec `dir="rtl"`).

## Développement

    npm install
    npm run dev      # http://127.0.0.1:5173/app/ — relais vers le back-office sur 8774
    npm run build    # écrit web/dist, servi par le back-office sous /app

Le back-office se lance en parallèle : `python -m minbar.backoffice --db <base> serve --port 8774`.

## Règles à tenir

1. Un seul formulaire par écran, découpé en onglets ou sous-onglets (la hauteur est jugée à l'œil).
2. Aucun chemin de la machine dans l'interface : libellés relatifs au programme.
3. Adresses en anglais, libellés en français (i18n).
4. Une bibliothèque standard plutôt qu'un équivalent maison.
5. Pas de travail jetable : un écran React n'arrive qu'avec son API d'écriture complète.

## Ordre de bascule

1. Connexion et tableau de bord — faits, en lecture.
2. Khutbas et clips en lecture, puis écritures : corrections, validation / rejet, régénération, publication.
3. Réglages (sept onglets) avec le sélecteur de dossiers côté serveur (`/api/v1/folders`).
4. Comptes et journal.
5. Retrait des gabarits Jinja, puis bascule du front de `/app` vers `/`.
