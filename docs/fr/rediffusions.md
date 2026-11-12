# Les rediffusions et leur envoi

Ce document s'adresse à l'administrateur de la mosquée. Il explique ce qu'est un clip, où il est
publié sur la machine, comment retrouver un clip dans les listes, et comment envoyer les
enregistrements du direct vers un compartiment de stockage — un réglage facultatif.

---

## 1. Ce qu'est un clip

Un clip est un **extrait court d'une khutba**, fabriqué après le direct à partir de l'enregistrement.
Il n'est pas découpé à la main : le traitement d'après-direct repère les passages qui se tiennent
seuls, les découpe et les sous-titre. Le nombre d'extraits demandés se règle dans
**Réglages → Traitement** (trois par défaut, de un à dix) ; le réglage s'applique au prochain
lancement du pipeline, pas aux clips déjà produits. La case **Produire des extraits après le
direct**, décochée, arrête le montage : la transcription, les notes et les versets sont quand même
produits, et le nombre saisi est conservé pour la réactivation.

| Caractère | Ce que cela donne |
| --- | --- |
| Format | vertical, 1080 × 1920, pour le téléphone |
| Durée | de 30 à 90 secondes |
| Sous-titres | en français seulement, incrustés dans l'image |
| Musique | aucune |
| Nombre | trois par défaut, réglable de 1 à 10 dans **Réglages → Traitement** |
| Types | verset, rappel, citation, passage, conclusion |
| Statut de départ | **À valider** |

Chaque clip porte un numéro et une version. Corriger les sous-titres puis régénérer produit une
nouvelle version, qui repasse par la validation : on ne publie jamais une version que personne n'a
relue.

Les statuts sont **À valider**, **Validé**, **Rejeté** et **Publié**. Un clip rejeté reste dans
l'historique ; il peut être remis dans la file de validation. La publication sur les réseaux est un
geste séparé, décrit dans **Publier sur Facebook, YouTube et Instagram**.

## 2. Le dossier local publié

Le connecteur **Dossier local** est actif par défaut. Il ne dépend d'aucun réseau : il copie le clip
validé et sa fiche dans le dossier de publication de la mosquée.

| Ce qui est déposé | Détail |
| --- | --- |
| Le clip | un fichier MP4, dans un sous-dossier portant la date de la khutba |
| La fiche | un fichier JSON du même nom, qui décrit le clip |

Le nom du fichier est stable : il reprend le nom de la mosquée, l'identifiant du direct, le numéro de
session, le numéro du clip, son type et sa version. C'est le même nom dans le dossier et derrière
l'adresse publique utilisée par Instagram.

La fiche JSON contient la mosquée, la khutba, la date, le direct d'origine, le numéro, le type, la
version, les secondes de début et de fin, la raison de la sélection, le nom du fichier source, la
légende, la personne qui a validé, celle qui a publié, la date de publication, l'absence de musique et
la mention « sous-titres en français seul ».

Le dossier se règle dans **Réglages → Dossiers**, ligne **Dossier de publication**. Un dossier est
proposé par défaut ; vous pouvez le changer. Par défaut, le même dossier sert au connecteur local et à
l'archivage Instagram ; chacun garde ensuite son propre champ.

La copie ne déplace pas l'original : le clip du dossier de travail reste en place.

## 3. Retrouver un clip : la pagination des listes

La page **Clips** affiche les clips du plus récent au plus ancien, par pages de **vingt**. En bas de
liste, une ligne dit où vous en êtes — par exemple « 20 sur 63 clips » — et un bouton
**Afficher les N suivants** ajoute la page suivante sans effacer la précédente.

Les onglets de statut filtrent la liste et affichent chacun leur nombre : **Tous**, **À valider**,
**Validés**, **Publiés**, **Rejetés**. Changer d'onglet repart de la première page ; le compteur du
bas se remet à jour sur le filtre choisi.

Deux autres listes ont leur propre limite :

| Liste | Ce qu'elle montre |
| --- | --- |
| **Publication → Historique** | les cent dernières tentatives de publication, sans pagination |
| **Journal** | les actions récentes, par pages de huit, avec Précédent et Suivant |

Une liste longue n'est pas un problème de stockage : les clips restent sur la machine même quand la
page n'en affiche que vingt.

## 4. L'envoi facultatif vers un compartiment

Après l'arrêt d'un direct, l'orchestrateur peut envoyer les **fichiers du direct** vers un
compartiment compatible S3. Il faut distinguer deux choses :

| Ce qui part où | Contenu |
| --- | --- |
| Compartiment de stockage | l'enregistrement complet du direct, et sa copie propre quand elle existe |
| Dossier de publication | les clips courts validés, avec leur fiche |

Le compartiment ne reçoit donc **pas** les clips. Il garde la matière brute du direct, pour l'archive
ou pour un autre usage.

La case **Envoyer les rediffusions** est **décochée par défaut**. Tant qu'elle l'est, aucun fichier ne
quitte la machine. C'est le réglage le plus sûr : un direct peut représenter plusieurs gigaoctets, et
l'envoi consomme la connexion de la mosquée. Vous l'activez quand vous savez où les fichiers doivent
aller et qui peut les lire.

Les champs, dans **Réglages → Rediffusions** :

| Champ | Ce qu'on y met |
| --- | --- |
| Service | l'adresse du service de stockage ; vide pour AWS |
| Compartiment | le nom du compartiment, en minuscules |
| Région | la région du service ; `auto` convient à Cloudflare R2 |
| Clé d'accès | l'identifiant de la clé créée chez le fournisseur |
| Clé secrète | le secret de cette clé, masqué après enregistrement |
| Dossier dans le compartiment | le préfixe des fichiers ; `rediffusions/` par défaut |
| Envoyer les rediffusions (option) | la case qui active réellement l'envoi |

Le bouton **Tester l'écriture** dépose un petit fichier texte dans le dossier choisi, puis le retire.
Il ne se contente pas de vérifier que le compartiment existe : il vérifie que la clé peut **écrire**,
puis **supprimer**. Le message de réussite nomme le compartiment et le préfixe.

Le réglage est lu au démarrage de l'orchestrateur. Une modification faite ensuite s'applique au
prochain direct, pas à celui qui tourne.

## 5. Préparer le compartiment chez le fournisseur

Le back-office ne crée ni le compartiment ni la clé : cela se fait chez le fournisseur, avant la
saisie.

| Fournisseur | Adresse du service | Région | À préparer |
| --- | --- | --- | --- |
| Amazon S3 | laisser vide | la région du compartiment, par exemple `eu-west-3` | un compartiment et une clé IAM limitée à ce compartiment |
| Cloudflare R2 | `https://<compte>.r2.cloudflarestorage.com` | `auto` | un compartiment et un jeton S3 avec droit d'écriture |
| MinIO, Backblaze | l'adresse du service, sans chemin | celle du service | un compartiment et une clé d'accès |

Trois règles évitent la plupart des échecs :

- **Le compartiment doit déjà exister.** Un nom inventé donne `NoSuchBucket`.
- **L'adresse du service ne prend pas de chemin.** Elle s'arrête à l'hôte ; le nom du compartiment a
  son propre champ.
- **La clé doit pouvoir écrire et supprimer.** Le test dépose puis retire un fichier ; une clé en
  lecture seule échoue alors que le compartiment est bon.

Le préfixe est un dossier dans le compartiment, pas un dossier sur la machine. Il n'y a rien à créer :
le préfixe apparaît avec les fichiers. `rediffusions/` les range à part, sans mélanger avec d'autres
usages du même compartiment.

Le compartiment n'a pas besoin d'être public : le back-office n'y lit rien depuis une page web. Un
compartiment privé est même préférable, puisqu'il contient l'image et le son d'une khutba.

## 6. Ce qui se passe en cas d'échec

Un échec d'envoi ne fait jamais tomber le direct : l'envoi part dans un fil séparé, après l'arrêt, et
le moteur n'attend pas le réseau.

| Message du journal | Cause probable | Ce qu'il faut faire |
| --- | --- | --- |
| `Le compartiment n'existe pas (nom ou compte).` | nom mal saisi, ou mauvais compte | vérifier le nom et l'adresse du service |
| `Clé d'accès inconnue du service.` | identifiant de clé faux ou supprimé | recréer une clé et la saisir |
| `Clé secrète refusée (signature invalide).` | secret faux, ou clé recopiée partiellement | ressaisir le secret en entier |
| `Le service refuse l'écriture : droits du jeton insuffisants.` | clé en lecture seule, ou droits limités | donner le droit d'écriture et de suppression |
| `Service injoignable : vérifiez l'adresse et le réseau.` | adresse du service fausse, ou réseau coupé | vérifier l'adresse, sans chemin, et la connexion |
| `Délai dépassé en joignant le service.` | réseau lent ou service bloqué | réessayer au prochain direct |
| `Nom de compartiment invalide pour ce service.` | majuscules, espaces, underscores | renommer en minuscules, chiffres, points et tirets |
| `Certificat refusé : vérifiez l'adresse (https).` | certificat du service, ou adresse en `http://` | utiliser l'adresse `https://` du service |

Ce qui n'est pas envoyé n'est pas perdu : les fichiers restent sur la machine. L'index local ne note
que les envois réussis, donc un fichier en échec est repris au prochain arrêt de direct. Un fichier
déjà envoyé et inchangé est sauté, ce qui évite de consommer la connexion deux fois.

Si le back-office est injoignable au démarrage, le journal dit
`réglage du compartiment injoignable … — envoi désactivé` : le direct se déroule normalement, sans
envoi.

## 7. Ce que l'administrateur doit vérifier

| Point | Ce que vous devez voir |
| --- | --- |
| La case | **Envoyer les rediffusions** cochée, puis réglages enregistrés |
| Le test | `Écriture acceptée dans « … » (…)` |
| Le premier envoi | dans le journal, `envoi de … vers compartiment/rediffusions/…`, puis `envoyée` |
| Les fichiers | dans le compartiment, sous le préfixe, avec le même nom que sur la machine |
| Les tailles | non nulles ; un fichier de 0 octet est ignoré et signalé |
| Les originaux | toujours présents sur la machine |

Le journal dit ce qui est parti. Un test réussi prouve que la clé écrit et supprime ; il ne prouve pas
que les fichiers du direct seront assez petits ou que la connexion tiendra.
Le premier vrai contrôle est donc l'après-premier-direct.

## 8. Questions fréquentes

**Le compartiment reçoit-il les clips ?**
Non. Il reçoit l'enregistrement du direct et sa copie propre. Les clips validés vont dans le dossier de
publication, puis, si vous le décidez, sur les réseaux.

**Puis-je vider le compartiment ?**
Oui. Aucune page du back-office ne lit son contenu : les clips et leurs fiches restent dans le dossier
local, et l'historique des publications reste dans la base. Vider le compartiment ne casse rien.

**Pourquoi deux fichiers pour un même direct ?**
L'orchestrateur enregistre le direct, et parfois une copie propre sans l'habillage des écrans. Les deux
partent quand elles existent, sous deux noms différents.

**J'ai changé de compartiment, les anciens fichiers ne remontent pas.**
C'est normal. L'index local retient quels fichiers sont déjà partis, sans distinguer la destination :
ils ne sont pas renvoyés vers le nouveau compartiment. Seuls les directs suivants y seront déposés.

**L'envoi supprime-t-il les fichiers de la machine ?**
Non. C'est une copie vers le compartiment ; les enregistrements et les clips restent en place.
