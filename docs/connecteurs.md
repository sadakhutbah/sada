# Publier sur Facebook, YouTube et Instagram

Ce document s'adresse à l'administrateur de la mosquée. Il explique ce qu'il faut obtenir auprès de
chaque plateforme, où saisir les identifiants, ce que fait le bouton de publication, et ce que le
connecteur ne fait pas.

---

## 1. La règle commune

Un connecteur relie un clip à un compte. Il ne publie jamais tout seul : il faut un clip **validé par
un humain**, puis un appui sur **Publier** dans la fiche du clip. Cette règle est vérifiée côté
serveur : un clip non validé est refusé, même si l'écran se trompe.

Le back-office propose quatre destinations :

| Connecteur | Ce qu'il fait | Identifiants nécessaires |
| --- | --- | --- |
| **Dossier local** | copie le clip et sa fiche dans un dossier de la machine | aucun ; actif par défaut |
| **Page Facebook** | dépose la vidéo sur la page de la mosquée | identifiant de la page, jeton de la page |
| **YouTube** | envoie la vidéo sur la chaîne de la mosquée | identifiant client, secret client, jeton d'actualisation |
| **Instagram** | publie un Reel sur le compte professionnel | identifiant du compte, jeton, adresse publique du dossier |

Sans identifiants, un connecteur reste **indisponible** et aucun appel n'est fait à la plateforme.
Un refus de la plateforme est enregistré tel quel dans l'historique : son message, pas une traduction.

Toute tentative, réussie ou refusée, laisse une trace dans **Publication → Historique** : le clip, le
connecteur, la date, la personne et le résultat. Un succès affiche l'adresse publique de la
publication ; un refus affiche le message de la plateforme. Pour le connecteur local, le résultat est
le chemin du fichier copié.

## 2. Facebook

### Ce qu'il faut obtenir

- une **page Facebook** pour la mosquée, et un compte qui en est administrateur ;
- l'**identifiant de la page** (un nombre, visible dans les réglages de la page) ;
- un **jeton d'accès de page** autorisé à publier des vidéos. Il se fabrique dans les outils de
  développeur de Meta, à partir d'un compte qui gère la page.

Un jeton de page obtenu depuis un jeton utilisateur de longue durée ne périme pas tout seul. Il tombe
en revanche si la personne change son mot de passe, révoque l'application ou perd son rôle sur la
page. Le back-office ne le renouvelle pas : il faudra en fabriquer un autre.

**Où le saisir.** Page **Publication**, onglet **Connecteurs**, carte **Page Facebook** :

| Champ | Ce qu'on y met |
| --- | --- |
| Identifiant de la page | le nombre de la page, pas son adresse web |
| Jeton d'accès de la page | le jeton, masqué après enregistrement |

Puis cochez **Activer ce connecteur** et enregistrez.

**Ce qui est publié.** La vidéo part avec la légende de la fiche, coupée à 5 000 caractères si elle
est plus longue. La vidéo est publique dès l'envoi. Le résultat est l'adresse de la publication.

## 3. YouTube

### Ce qu'il faut obtenir

- la **chaîne** de la mosquée, et un compte Google qui peut y publier ;
- un projet dans Google Cloud avec l'**API YouTube Data** activée ;
- un **client OAuth** de type application de bureau, avec son identifiant et son secret ;
- un **jeton d'actualisation** obtenu une fois avec l'accès hors ligne. C'est ce jeton qui permet
  d'envoyer sans redemander une connexion à chaque fois.

Le jeton d'actualisation se fabrique hors du back-office, avec les outils Google. S'il est révoqué,
expiré ou supprimé, l'envoi échoue avec `invalid_grant` et il faut en produire un nouveau.

**Où le saisir.** Même endroit, carte **YouTube** :

| Champ | Ce qu'on y met |
| --- | --- |
| Identifiant de la chaîne | facultatif ; sert de repère, c'est le jeton qui choisit la chaîne |
| Identifiant client OAuth | l'identifiant du client créé dans Google Cloud |
| Secret client OAuth | son secret, masqué après enregistrement |
| Jeton d'actualisation | le jeton hors ligne, masqué après enregistrement |
| Visibilité | `public`, `unlisted` ou `private` ; `public` par défaut |
| Catégorie | `27` (éducation) par défaut |

**Ce qui est publié.** Le titre est construit automatiquement : le titre des clips et le nom de la
mosquée, puis la date, coupé à 100 caractères. La légende sert de description, coupée à 4 900
caractères. L'envoi peut durer jusqu'à trente minutes ; s'il échoue, rien n'est publié et le message
est enregistré.

YouTube limite les envois : un envoi coûte environ 1 600 unités sur les 10 000 quotidiennes, soit
environ six publications par jour. Au-delà, la plateforme répond `quotaExceeded` jusqu'au lendemain.

## 4. Instagram

### Ce qu'il faut obtenir

- un **compte professionnel** Instagram (entreprise ou créateur), relié à une page Facebook ;
- l'**identifiant du compte professionnel** (un nombre, pas le nom d'utilisateur) ;
- un **jeton d'accès** autorisé à publier du contenu sur ce compte ;
- une **adresse publique** du dossier de publication : une adresse web qui sert les fichiers déposés
  par la mosquée, sans mot de passe.

Instagram ne reçoit pas de fichier. Son API **télécharge la vidéo elle-même** depuis l'adresse
publique. Le back-office copie donc d'abord le clip dans le dossier de publication, puis construit
l'adresse `adresse-publique/date/nom-du-clip.mp4` et la donne à Instagram.

**Où le saisir.** Même endroit, carte **Instagram** :

| Champ | Ce qu'on y met |
| --- | --- |
| Identifiant du compte professionnel | le nombre du compte, pas son nom |
| Jeton d'accès | le jeton, masqué après enregistrement |
| Adresse publique du dossier | l'adresse qui sert le dossier de publication |
| Dossier de publication | celui où le clip est copié avant l'envoi ; proposé par défaut |

Si l'adresse publique ne commence pas par `http://` ou `https://`, le connecteur reste indisponible et
le dit. C'est volontaire : sans adresse valide, Instagram ne pourrait rien télécharger.

**Ce qui est publié.** Un Reel vertical, avec la légende saisie, coupée à 2 200 caractères, partagé
dans le fil du compte.
La plateforme met quelques secondes à préparer la vidéo ; le back-office attend jusqu'à deux minutes.
Au-delà, l'envoi est refusé avec `délai dépassé` et rien n'est publié.

La vidéo doit rester accessible pendant tout ce temps. Un fichier servi seulement sur le réseau local,
ou derrière une page de connexion, ne sera pas téléchargé par Instagram.

## 5. Où saisir les identifiants, et ce que l'écran affiche

Le chemin est **Publication → onglet Connecteurs**. Le lien **Réglages → Publication** y renvoie
directement : il n'y a pas de formulaire séparé dans les réglages.

Chaque carte affiche :

| Élément | Ce qu'il veut dire |
| --- | --- |
| **Disponible** | les identifiants obligatoires sont enregistrés et le connecteur est activé |
| **Indisponible** | le connecteur ne publiera pas ; le motif est écrit sous la description |
| **Configurer** | ouvre les champs et la case d'activation ; réservé aux administrateurs |

Les motifs d'indisponibilité sont précis : par exemple `désactivé : identifiants absents (page_id,
page_token)`, `désactivé par l'administrateur`, ou l'adresse publique qui n'est pas une URL.

Un champ secret n'est jamais réaffiché. Il apparaît masqué ; le laisser vide ou renvoyer les points
garde la valeur enregistrée. Le saisir à nouveau la remplace.

**Il n'y a pas de bouton Tester pour les trois réseaux.** Enregistrer ne fait qu'écrire des valeurs
dans la base : cela ne prouve rien. Le seul contrôle est une publication, et elle est réelle — elle
apparaît en ligne. Faites-la sur un clip que vous acceptez de voir publié ; le résultat est dans
l'historique. Le dossier local se vérifie, lui, en regardant les fichiers copiés.

## 6. Ce que les connecteurs ne font pas

- **Pas de programmation.** Une publication part au moment de l'appui, pas à une heure choisie.
- **Pas de republication automatique.** Rien ne repart tout seul après un échec. Un clip déjà publié
  peut être renvoyé si vous appuyez de nouveau : l'historique garde les deux tentatives, et la
  plateforme aura deux vidéos. Ne le faites pas sans raison.
- **Pas de modification ni de suppression** sur la plateforme. Corriger une légende publiée se fait
  dans Facebook, YouTube ou Instagram, pas ici.
- **Pas de retouche de la vidéo.** Le fichier du clip part tel quel ; aucun redimensionnement, aucun
  changement de format.
- **Pas de statistiques.** Les vues, commentaires et abonnés ne remontent pas dans le back-office.
- **Pas de renouvellement des jetons.** Facebook et Instagram ne se reconnectent pas seuls ; YouTube
  renouvelle son jeton d'accès à partir du jeton d'actualisation, mais ne fabrique pas ce dernier.
- **Pas de multi-comptes.** Un seul jeu d'identifiants par mosquée et par réseau : le dernier
  enregistré gagne.

## 7. Erreurs fréquentes

Le message de la plateforme est recopié dans l'historique. Les cas qui reviennent :

| Message | Cause probable | Ce qu'il faut faire |
| --- | --- | --- |
| `HTTP 400 — … Invalid OAuth access token` | jeton Facebook ou Instagram expiré, révoqué ou recopié de travers | fabriquer un nouveau jeton et le saisir |
| `HTTP 403 — … (#200) … permissions` | le compte n'a pas le droit de publier sur la page ou le compte | vérifier le rôle sur la page et les droits demandés au jeton |
| `HTTP 400 — … (#100) … param` | identifiant de page ou de compte incorrect | reprendre le nombre exact, pas le nom |
| `jeton Google : HTTP 400 — invalid_grant` | jeton d'actualisation expiré ou révoqué | en produire un nouveau avec l'accès hors ligne |
| `HTTP 403 — quotaExceeded` | quota YouTube du jour épuisé | attendre le lendemain, ou demander une augmentation du quota |
| `HTTP 400 — Media download failed` | Instagram n'a pas pu lire l'adresse publique | vérifier l'adresse, le dossier et le nom du fichier |
| `Instagram n'a pas fini de préparer la vidéo (délai dépassé)` | préparation trop longue ou fichier refusé | réessayer ; si cela se répète, contrôler le fichier |
| `désactivé : identifiants absents (…)` | un champ obligatoire est vide | remplir les champs nommés et enregistrer |
| `clip non validé : publication refusée` | le clip n'est pas au statut Validé, ou doit être régénéré | valider le clip avant de publier |
| `fichier du clip introuvable` | le fichier a été déplacé ou supprimé | régénérer le clip, puis le valider |

Une erreur `erreur : …` suivie d'un texte technique vient d'un incident réseau ou d'un fichier
illisible. Réessayer est sans danger : une tentative échouée ne publie rien.

## 8. Questions fréquentes

**Faut-il un compte professionnel pour Facebook et YouTube ?**
Non. Facebook demande une page et un jeton de page ; YouTube demande une chaîne et un client OAuth.
Seul Instagram exige un compte professionnel, relié à une page Facebook.

**Puis-je publier le même clip sur les trois réseaux ?**
Oui, une publication par réseau, depuis la même fiche. Chaque appui est indépendant : un refus sur
Instagram n'empêche pas Facebook ou YouTube de réussir.

**Pourquoi le connecteur est-il indisponible alors que j'ai enregistré ?**
Trois causes : un champ obligatoire est vide, la case **Activer ce connecteur** n'est pas cochée, ou
l'adresse publique d'Instagram n'est pas une adresse web. Le motif est écrit sous la description.

**La légende est-elle la même partout ?**
Oui, à la coupure près : 5 000 caractères sur Facebook, 4 900 sur YouTube, 2 200 sur Instagram. Le
texte proposé reprend le titre des clips, la date, la khutba et les versets cités ; vous pouvez le
modifier avant chaque publication.
