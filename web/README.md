# Front React du back-office

Vite + React 19 + TypeScript + React Router 7 + `react-i18next` (français par défaut, anglais,
arabe prévu avec `dir="rtl"`) + **Tailwind CSS 4**.

La charte est reprise du back-office en gabarits, à l'identique : papier `#faf9f4`, encre verte,
feuille `#8dc63f`, rouge direct `#d7372c`, Instrument Serif pour les titres, Source Sans 3 pour le texte,
Amiri pour l'arabe. Les jetons sont déclarés une fois dans `src/styles.css` (`@theme`) :
on écrit `bg-paper`, `text-deeper`, `border-line`, `font-serif`, jamais une couleur en dur.
Les composants communs (carte, pastille, sélecteur segmenté, champ, bouton) sont dans `src/ui.tsx`.

## Développement

    npm install
    npm run dev      # http://127.0.0.1:5173/ — relais vers le back-office sur 8774
    npm run build    # écrit web/dist, servi par le back-office à la racine

Le back-office se lance en parallèle : `python -m minbar.backoffice --db <base> serve --port 8774`.

## Règles à tenir

1. Un seul formulaire par écran, découpé en onglets ou sous-onglets (la hauteur est jugée à l'œil).
2. Aucun chemin de la machine dans l'interface : libellés relatifs au programme.
3. Adresses en anglais, libellés en français (i18n).
4. Une bibliothèque standard plutôt qu'un équivalent maison.
5. Pas de travail jetable : un écran React n'arrive qu'avec son API d'écriture complète.

## Interface unique

Les gabarits Jinja ont été retirés : ce front est la seule interface du back-office, servi à la racine.
Le serveur ne produit plus que du JSON (`/api/v1`), les médias et le logo ; les écrans sont décrits ici,
une seule fois. `web/dist` est versionné pour que le back-office serve le front sans étape de build.
