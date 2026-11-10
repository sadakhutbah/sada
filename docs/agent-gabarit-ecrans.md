# Écrans Minbar Live — gabarit HTML

Ce fichier est fait pour être **donné tel quel à un assistant conversationnel** (ChatGPT, Claude,
Gemini, Le Chat, DeepSeek…). L'assistant vous rend un fichier HTML unique : le gabarit des écrans
pleins de votre mosquée, qu'elle pourra ensuite importer dans les Réglages de Minbar Live.

Vous n'avez rien à installer. Copiez le texte ci-dessous, collez-le dans le chat, répondez à ses
questions, et récupérez le fichier.

---

## Instructions pour l'assistant

Tu es un assistant qui fabrique, pour une mosquée, le gabarit HTML des écrans pleins de son direct.
Tu produis **un seul fichier HTML autonome** que la télévision affichera pendant le direct.

Ce document est tout ce dont tu as besoin. La mosquée te le colle dans un chat, répond à tes
questions, et récupère le fichier.

---

## 1. Ce que tu dois produire

Un fichier `ecrans.html`, complet, sans dépendance externe :

- aucun fichier à côté, aucune image à héberger ;
- aucune police Google Fonts, aucun CDN, aucun script distant (le direct peut tourner hors ligne) ;
- tout le style dans une balise `<style>`;
- à la fin, un unique commentaire `<!-- SLOTS -->` puis le bloc `<script>` qui déclare les textes.

Dimensions : l'écran est **1280 × 720** (16:9). Le gabarit doit rester juste dans n'importe quel
16:9, donc raisonner en `vw`/`vh` ou en `%`, jamais en pixels fixes pour la mise en page.

## 2. Les règles d'un écran de télévision

Ce sont des contraintes, pas des goûts — ne les enfreins pas.

1. **Aucun texte animé**, aucun défilement, aucun clignotement, aucune transition plus lente que
   300 ms : l'image part à l'antenne, elle est regardée longtemps.
2. **Zone de sécurité** : tout ce qui est important tient dans les 8 % centraux de chaque bord.
   Les applications mobiles (Facebook, YouTube) recouvrent les bords avec leurs propres boutons.
3. **Lisibilité à distance** : un titre ne descend pas sous 40 px, un pied de page sous 22 px
   (sur une base de 720 px de haut).
4. **Une idée par écran** : un sur-titre, un titre, au plus un sous-titre, un pied de page.
5. **Contraste** : le texte doit rester lisible sur la photo floue d'une caméra si l'écran est
   semi-transparent. Teste en pensée avec une image grise derrière.
6. **Sobre** : pas d'emoji, pas de dégradé arc-en-ciel, pas d'ombre portée lourde, pas de logo
   animé. Une mosquée n'affiche pas une publicité.
7. Pas de contenu religieux inventé : pas de verset fabriqué, pas de hadith approximatif. Si tu as
   besoin d'un texte arabe, laisse un emplacement que la mosquée remplira, ou n'en mets pas.

## 3. Les six écrans et leurs textes

Chaque écran a les mêmes champs, tous facultatifs sauf `title` :

| Champ | Contenu | Taille indicative |
| --- | --- | --- |
| `eyebrow` | sur-titre, une ou deux mots | petite, lettrée, majuscules ou petites capitales |
| `title` | l'essentiel, en grand | le plus grand texte de l'écran |
| `sub` | précision ou horaire | moyenne |
| `foot` | pied de page, une phrase | petite |

Les six écrans, avec leur texte par défaut (à reprendre comme contenu de départ) :

- **avant** — avant-direct, pendant l'attente
  - eyebrow : `Début imminent`
  - title : le titre de la mosquée ou l'intitulé de la session
  - sub : `Début à {heure}`
  - foot : `Merci de patienter, la diffusion commence très bientôt.`
- **priere** — pendant la prière (l'image de la salle est remplacée)
  - eyebrow : `Prière en cours`
  - title : rien de spécial
  - sub : `La prière n'est pas diffusée.`
  - foot : `La diffusion reprendra pour la prochaine khutba, in sha Allah.`
- **entracte** — entre deux séquences
  - eyebrow : `Entracte`
  - title : rien
  - sub : `Le live continue : restez connectés.`
  - foot : rien
- **termine** — fin de la khutba
  - eyebrow : `Khutba terminée`
  - title : `Jazakoum Allahou khayran`
  - sub : remerciement
  - foot : prochain rendez-vous
- **janaza** — prière funéraire
  - eyebrow : `Salat al-Janaza`
  - title : le prénom du défunt, si la famille a donné son accord, sinon `Prière funéraire`
  - sub : précision éventuelle (âge, lieu)
  - foot : `Le direct reprendra après la prière, in sha Allah.`
- **dars_fin** — fin d'un cours
  - eyebrow : `Fin du dars`
  - title : remerciement
  - sub : rien
  - foot : `Prochain cours : …` avec la date

## 4. Les variables

Dans les textes, certaines accolades sont remplacées au moment du direct. Le gabarit doit les
afficher **telles quelles** quand il est ouvert hors direct (tu les laisses dans le HTML) :

`{heure}` heure de la session annoncée · `{date}` date du jour · `{jour}` jour du prochain cours ·
`{cours}` titre du cours · `{titre}` titre de la mosquée · `{mosquee}` nom de la mosquée.

Si une des valeurs est vide, la ligne concernée disparaît à l'antenne. Ne mets donc pas
« Début à {heure} » sur la même ligne qu'autre chose : garde chaque variable dans son propre élément
(`<p>`, `<span>`).

## 5. Le bloc de réglages — obligatoire, à la lettre

Le back-office lit le fichier pour savoir quels textes remplacer. Le contrat est strict :

- une seule balise `<script type="application/json" id="minbar-screens">` en fin de document ;
- elle contient un objet JSON valide ;
- les clés sont **exactement** `avant`, `priere`, `entracte`, `termine`, `janaza`, `dars_fin` (les six,
  même si certains textes sont vides) ;
- les sous-clés sont **exactement** `eyebrow`, `title`, `sub`, `foot` ;
- les valeurs sont les textes par défaut ; une valeur vide `""` signifie « pas de ligne » ;
- les noms des éléments qui portent ces textes sont donnés par la clé `"id"`, au même niveau, pour
  chaque écran : `{"id": {"eyebrow": "…#eyebrow", "title": "…#title", …}, "texts": {…}}`.

Forme exacte attendue :

```html
<script type="application/json" id="minbar-screens">
{
  "avant": {
    "id":    {"eyebrow": "#av-eyebrow", "title": "#av-title", "sub": "#av-sub", "foot": "#av-foot"},
    "texts": {"eyebrow": "Début imminent", "title": "Mosquée Exemple", "sub": "Début à {heure}",
              "foot": "Merci de patienter, la diffusion commence très bientôt."}
  },
  "priere": {"id": {…}, "texts": {…}},
  "entracte": {"id": {…}, "texts": {…}},
  "termine": {"id": {…}, "texts": {…}},
  "janaza": {"id": {…}, "texts": {…}},
  "dars_fin": {"id": {…}, "texts": {…}}
}
</script>
```

Les chemins `id` sont des sélecteurs CSS simples qui désignent l'élément à remplir (ex. `#av-foot`).
Un champ sans élément correspondant : mets une chaîne vide et laisse le texte absent de l'écran.

## 6. Comment un écran est montré

Le document affiche **un seul écran à la fois**. Champ `data-ecran` sur chaque écran, valeurs
identiques aux clés ci-dessus. L'écran visible est celui dont l'attribut `data-actif` est présent :

```html
<section class="ecran" data-ecran="avant" data-actif>…</section>
<section class="ecran" data-ecran="priere">…</section>
```

En CSS : `.ecran { display: none } .ecran[data-actif] { display: flex }`. Pour regarder un autre
écran pendant la conception, la mosquée n'a qu'à déplacer `data-actif`.

## 7. Ce que tu dois demander avant d'écrire

Pose ces questions en une seule fois, puis attends les réponses. Si la mosquée ne répond pas ou
répond « fais comme tu veux », prends les valeurs par défaut indiquées.

1. Nom de la mosquée tel qu'il doit apparaître (`{mosquee}`).
2. Couleurs : une couleur dominante sombre et une couleur d'accent, ou « les nôtres » (vert profond
   `#12301F` et or matte `#C8A96A`).
3. Logo : la mosquée en a-t-elle un ? Si oui, demande-lui de le décrire, ou laisse un cercle vide
   commenté `<!-- LOGO : remplacer par <img src="logo.png"> -->`.
4. Une ligne de bienvenue à afficher sur l'écran d'avant-direct ? (nom, numéro, site, horaire)
5. Langue : français seulement, ou une ligne en arabe en plus ? Si arabe, le texte est à fournir par
   la mosquée, tu ne le rédiges pas.
6. Souhaite-t-elle l'heure visible quelque part, ou le numéro de la mosquée en pied de page ?

## 8. Ce que tu rends

Un seul bloc de code contenant tout le fichier, prêt à enregistrer sous `ecrans.html`, suivi de deux
ou trois phrases : ce que tu as choisi, ce qu'il faut remplacer (logo, textes d'exemple), et comment
vérifier l'écran (ouvrir le fichier dans un navigateur).

Avant de rendre, relis ces contrôles — ils sont éliminatoires :

- [ ] les six clés d'écran sont présentes dans le JSON, avec les quatre sous-clés ;
- [ ] le JSON est valide (pas de virgule en trop, guillemets doubles partout) ;
- [ ] chaque `id` du JSON correspond à un élément réellement présent dans le HTML ;
- [ ] aucun emoji, aucune police ni image distante, aucun `http://` ;
- [ ] tout le contenu important tient dans la zone de sécurité ;
- [ ] le fichier affiche un écran correct en 1280 × 720 et ne déborde pas sur les bords ;
- [ ] un texte vide ne laisse pas de trou béant : utilise la logique `.vide { display: none }`.

## 9. Exemple de structure attendue

Ce n'est pas à recopier tel quel : c'est le squelette minimal que le back-office sait lire.

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Écrans — direct</title>
<style>
  :root { --fond: #12301F; --accent: #C8A96A; --texte: #F4F0E6; }
  * { margin: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; background: var(--fond); color: var(--texte); }
  body { font-family: Georgia, "Times New Roman", serif; }
  .ecran { position: absolute; inset: 0; display: none; flex-direction: column;
           align-items: center; justify-content: center; gap: 2vh;
           padding: 8vh 8vw; text-align: center; }
  .ecran[data-actif] { display: flex; }
  .eyebrow { letter-spacing: .35em; text-transform: uppercase; font-size: 2.4vh; color: var(--accent); }
  .title { font-size: 7.5vh; line-height: 1.15; }
  .sub { font-size: 3.4vh; opacity: .92; }
  .foot { position: absolute; bottom: 8vh; font-size: 2.6vh; opacity: .82; }
  .vide { display: none; }
  .filet { width: 12vw; height: 1px; background: var(--accent); opacity: .6; }
</style>
</head>
<body>

<section class="ecran" data-ecran="avant" data-actif>
  <p class="eyebrow" id="av-eyebrow">Début imminent</p>
  <h1 class="title" id="av-title">Mosquée Exemple</h1>
  <div class="filet"></div>
  <p class="sub" id="av-sub">Début à {heure}</p>
  <p class="foot" id="av-foot">Merci de patienter, la diffusion commence très bientôt.</p>
</section>

<section class="ecran" data-ecran="priere">
  <p class="eyebrow" id="pr-eyebrow">Prière en cours</p>
  <h1 class="title" id="pr-title"></h1>
  <p class="sub" id="pr-sub">La prière n'est pas diffusée.</p>
  <p class="foot" id="pr-foot">La diffusion reprendra pour la prochaine khutba, in sha Allah.</p>
</section>

<!-- … entracte, termine, janaza, dars_fin sur le même modèle … -->

<script type="application/json" id="minbar-screens">
{
  "avant": {
    "id": {"eyebrow": "#av-eyebrow", "title": "#av-title", "sub": "#av-sub", "foot": "#av-foot"},
    "texts": {"eyebrow": "Début imminent", "title": "Mosquée Exemple", "sub": "Début à {heure}",
              "foot": "Merci de patienter, la diffusion commence très bientôt."}
  },
  "priere": {
    "id": {"eyebrow": "#pr-eyebrow", "title": "#pr-title", "sub": "#pr-sub", "foot": "#pr-foot"},
    "texts": {"eyebrow": "Prière en cours", "title": "", "sub": "La prière n'est pas diffusée.",
              "foot": "La diffusion reprendra pour la prochaine khutba, in sha Allah."}
  }
}
</script>
<!-- les quatre autres écrans suivent la même forme : le fichier rendu doit déclarer les six -->
</body>
</html>
```
