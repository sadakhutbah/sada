# Les écrans du direct — mode d'emploi

Ce document s'adresse à **la personne qui administre Minbar Live dans une mosquée**. Pas de
vocabulaire technique, pas de prérequis : si vous savez remplir un formulaire, vous savez faire.

---

## 1. À quoi servent ces écrans

Pendant un direct, la caméra ne montre pas toujours quelque chose d'utile : avant que la khutba
commence, pendant que les gens s'installent, entre deux séquences, quand la salle se vide. Un écran
plein — une image avec un texte, à la place de la vidéo — évite de diffuser une salle vide ou des
gens qui ne veulent pas être filmés.

Dans Minbar Live, on appelle cela un **écran**. Il y en a six, chacun pour un moment précis du
direct. Le moteur les affiche automatiquement, vous n'avez rien à déclencher.

Vous pouvez changer **les textes** de ces écrans. Vous ne changez pas leur mise en page : nous nous en
occupons pour que ce soit lisible sur une télévision et sur un téléphone.

---

## 2. Les six moments, et lequel dire quoi

| Écran | Quand il apparaît | Ce qu'on y met d'habitude |
| --- | --- | --- |
| **Avant** | avant le début, pendant l'attente | le nom de la mosquée, l'heure de début, un mot de patience |
| **Prière** | pendant la prière, dont l'image n'est jamais diffusée | « la prière n'est pas diffusée », et quand ça reprend |
| **Entracte** | entre deux séquences | un mot pour dire que ça continue |
| **Terminé** | à la fin de la khutba | un remerciement, le prochain rendez-vous |
| **Janaza** | pour une prière funéraire | le nom du défunt *si la famille est d'accord*, sinon rien |
| **Fin de cours** | à la fin d'un dars | un remerciement, la date du prochain cours |

Un écran qui ne vous concerne pas se laisse vide : il ne s'affichera pas, ou gardera son texte
d'origine. Rien n'est obligatoire.

**Un écran n'est pas un panneau d'information.** Il est vu de loin, parfois sur un téléphone
tenu à la main, cinq secondes. Trois lignes suffisent ; au-delà, personne ne lit.

---

## 3. Remplir un écran, geste par geste

1. Menu de gauche → **Réglages**.
2. En haut, l'onglet **Écrans**.
3. Une rangée de sous-onglets : *Avant*, *Prière*, *Entracte*, *Terminé*, *Janaza*, *Fin de cours*.
   Cliquez sur celui que vous voulez modifier.
4. Quatre champs s'affichent, du plus petit en haut au plus grand en bas. À droite, l'aperçu montre
   l'écran tel qu'il partira à l'antenne — il se met à jour pendant que vous tapez.
5. Tapez votre texte. Le texte gris dans un champ vide, c'est ce qui s'affiche **par défaut** : si
   vous ne voulez rien changer, ne touchez à rien.
6. Quand vous avez fini plusieurs écrans, cliquez en bas sur **Enregistrer les réglages**.

Pour **revenir au texte d'origine** d'un champ : effacez-le complètement, puis enregistrez. Un champ
vide ne veut pas dire « rien à l'écran » : il veut dire « remets ce qui était écrit avant ».

---

## 4. Les accolades : `{heure}`, `{date}`, `{jour}`…

Certains textes contiennent des accolades. Ce ne sont pas des fautes de frappe : Minbar Live les
remplace au moment du direct.

- `{heure}` → l'heure de la session, par exemple « Début à **{heure}** » devient « Début à **19h30** ».
- `{date}` → la date du jour, écrite en toutes lettres.
- `{jour}` → le jour de la semaine du prochain rendez-vous.
- `{cours}` → le titre du cours qui va commencer ou qui vient de finir.
- `{titre}` → le titre officiel de la session (khutba, dars, conférence).
- `{mosquee}` → le nom de votre mosquée, tel qu'il est enregistré dans Réglages → Mosquée.

Attention à un piège : si l'information n'existe pas ce jour-là, l'accolade disparaît purement et
simplement. Une phrase conçue autour d'elle peut donc devenir bancale. Le cas typique : « Début à
{heure} » quand aucune heure n'est renseignée donne « Début à ». Mettez l'accolade dans une phrase
qui tient debout toute seule : « La diffusion commence à {heure}. »

---

## 5. Ce que ces écrans ne savent pas faire

- **Changer les couleurs, la police, la place du logo.** C'est la mise en page, elle est commune à
  toutes les mosquées.
- **Afficher une image, une photo, une annonce.** Ces écrans sont du texte.
- **Montrer un verset ou un hadith que vous n'avez pas écrits vous-même.** Aucun texte religieux
  n'est fourni par défaut : ce que vous écrivez est ce qui s'affiche, à vous de le vérifier.
- **S'appliquer rétroactivement.** Les textes sont lus **au démarrage du direct**. Si vous les
  modifiez pendant un direct en cours, ils changeront au direct suivant, ou après un redémarrage du
  direct. Ce n'est pas un bug.

---

## 6. Se faire un visuel entièrement à vous

Les six écrans de base ont un dessin commun, celui de Minbar Live. Une mosquée qui veut **son propre
visuel** — ses couleurs, sa police, son logo, sa composition — peut le faire fabriquer par un
assistant conversationnel.

Le principe : on donne à l'assistant une consigne écrite, il rend un fichier HTML, et ce fichier
devient votre gabarit d'écrans.

1. Réglages → Écrans : la page affiche une **adresse** (un lien qui finit par
   `agent-gabarit-ecrans.md`). Copiez-la avec le bouton prévu.
2. Ouvrez ChatGPT, Claude, Gemini, ou n'importe quel assistant : collez cette adresse dans le chat et
   demandez-lui de suivre les instructions. S'il ne peut pas ouvrir de lien, ouvrez l'adresse dans
   votre navigateur, sélectionnez tout le texte de la page, et collez-le.
3. L'assistant vous posera quelques questions (nom de la mosquée, couleurs, logo, langue, ligne de
   bienvenue). Répondez. Si vous répondez « fais comme tu veux », il prendra des choix neutres.
4. Il vous rend un fichier nommé `ecrans.html`. Récupérez-le.

Ce fichier reste votre propriété : il ne contient que du texte et des couleurs, il tourne sans
internet, et il ne fait rien d'autre que s'afficher.

Une limite à connaître : ce gabarit est une maquette d'aspect, pas encore branchée au direct. Faire
apparaître vos textes en direct sur votre propre gabarit est une étape à part — parlez-en avant de
compter dessus.

---

## 7. Vérifier que votre texte est correct

1. Regardez l'aperçu à droite : c'est exactement ce que verra le spectateur.
2. Vérifiez qu'aucune ligne ne dépasse — un texte trop long est réduit ou coupé, l'aperçu le montre.
3. Demandez-vous si vous comprendriez cette phrase **sur un téléphone, à un mètre, sans son**. Si
   non, raccourcissez.
4. Après le premier direct où vous les avez changés, regardez le fichier en replay : c'est le seul
   juge.

---

## 8. Quelques exemples de textes, à adapter

- Avant une khutba : *(sur-titre)* `Vendredi` · *(titre)* `Khotbat al-Jumu'a` · *(sous-titre)*
  `Début à {heure}` · *(pied)* `Merci de couper votre téléphone.`
- Avant un cours : *(sur-titre)* `Dars du soir` · *(titre)* `{titre du cours}` · *(sous-titre)*
  `Début à {heure}` · *(pied)* `Les questions se posent à la fin.`
- Fin d'un cours : *(sur-titre)* `Fin du dars` · *(titre)* `Jazakoum Allahou khayran` · *(pied)*
  `Prochain cours : {jour} à {heure}.`
- Janaza : *(sur-titre)* `Salat al-Janaza` · *(titre)* `Prière funéraire` · *(pied)*
  `Le direct reprendra après la prière.`

---

## 9. Questions fréquentes

**Un champ vide, c'est un écran sans ce texte ?**
Non. Un champ vide remet le texte par défaut. Pour qu'une ligne disparaisse vraiment, il faut la
vider dans le gabarit lui-même, pas dans le formulaire.

**Mes modifications n'apparaissent pas pendant le direct.**
Elles s'appliquent au démarrage du direct suivant. C'est voulu : un texte ne doit pas changer au
milieu d'une khutba.

**J'ai tout effacé par erreur.**
Rechargez la page sans enregistrer : vos textes enregistrés reviennent. Si vous avez déjà enregistré,
il faut retaper.

**Puis-je avoir deux jeux de textes, un pour le vendredi et un pour les cours ?**
Pas encore. Les textes sont communs à la mosquée. Une évolution est possible.

**Qui d'autre peut modifier ces textes ?**
Les comptes qui ont accès aux Réglages, c'est-à-dire l'administrateur de la mosquée.
