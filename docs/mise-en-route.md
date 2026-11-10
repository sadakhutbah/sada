# Mettre le direct en route

Ce document s'adresse à l'administrateur de la mosquée. Il décrit la première connexion au
back-office, la visite de mise en route en quatre écrans, le branchement de la caméra ou d'OBS, les
destinations du direct, et ce qui se passe au premier direct.

---

## 1. Avant de commencer : le compte, la mosquée, le fuseau

L'installation a déjà créé la mosquée et votre compte administrateur. Vous devez avoir reçu :

| Ce qu'il vous faut | À quoi cela sert |
| --- | --- |
| L'adresse du back-office | ouvrir l'interface dans un navigateur, depuis un ordinateur ou un téléphone du réseau |
| Votre identifiant et votre mot de passe | vous connecter ; le rôle administrateur ouvre tous les réglages |
| Le nom exact de la mosquée | il apparaît sur les écrans et dans les légendes publiées |
| Le nom officiel du fuseau horaire | par exemple `Europe/Paris` ; il règle l'heure des écrans et du programme |
| L'adresse RTSP de la caméra, ou un ordinateur avec OBS | alimenter l'image du direct |
| Les adresses RTMP des réseaux | une par destination : page Facebook, chaîne YouTube, autre service |

Deux programmes tournent sur la machine : le back-office, qui affiche cette interface, et
l'orchestrateur, qui fait le direct. Le back-office peut rester ouvert sans direct ; l'orchestrateur,
lui, n'a pas d'écran. La mise en route ne concerne que les réglages lus par l'orchestrateur à son
démarrage.

La visite de mise en route ne demande pas le nom de la mosquée : il a été saisi à l'installation.
Vérifiez-le avant tout, dans **Réglages → Mosquée**.

| Champ | Ce qu'il change |
| --- | --- |
| **Nom de la mosquée** | les écrans pleins, les légendes, les titres envoyés aux réseaux |
| **Fuseau horaire** | l'heure affichée sur les écrans, les horaires du programme et les dates du journal |
| **Titre des clips** | la première ligne des clips et des légendes proposées à la publication |
| **Adresse Mawaqit** | facultative ; elle donne les horaires de prière de la mosquée |
| **Adresse publique** | l'adresse par laquelle la mosquée ouvre ce back-office, utilisée dans les liens de documentation |

Le fuseau doit être le **nom officiel** de la zone, pas un décalage. `Europe/Paris` est proposé par
défaut ; un nom inconnu est refusé à l'enregistrement. Un mauvais fuseau ne coupe pas le direct, mais
il décale les écrans et le programme d'une heure ou plus.

Cliquez sur **Enregistrer les réglages** en bas de la page. La page signale les modifications non
enregistrées ; tant qu'elles ne sont pas enregistrées, l'orchestrateur ne les verra pas.

## 2. La visite en quatre écrans

À la première connexion d'un administrateur, le back-office ouvre la mise en route. Elle ne réapparaît
plus une fois terminée ou passée. Quatre écrans, dont un facultatif :

| Écran | Ce que vous faites | Ce qui est enregistré en quittant l'écran |
| --- | --- | --- |
| **1. Entrée vidéo** | vous choisissez la caméra de la mosquée ou la poussée depuis un ordinateur | le mode et l'adresse lue par l'orchestrateur |
| **2. Destinations** | vous ajoutez les adresses RTMP des réseaux | la liste complète, avec les cases actives |
| **3. Rediffusions** | facultatif : vous branchez un compartiment de stockage | le compartiment, seulement si vous l'avez rempli |
| **4. Terminé** | vous relisez le résumé | rien : la mise en route est marquée comme faite |

Le bouton **Continuer** enregistre l'écran courant avant de passer au suivant. Si une valeur est
refusée, le message s'affiche en rouge et vous restez sur l'écran : rien n'est perdu, corrigez le
champ fautif.

Le lien **Passer la mise en route** termine la visite immédiatement, sans enregistrer l'écran
courant. Les mêmes réglages restent accessibles ensuite dans **Réglages → Direct** et **Réglages →
Rediffusions** ; la visite n'est pas le seul chemin.

## 3. Brancher la caméra

Choisissez **Caméra de la mosquée** si la caméra diffuse déjà son image sur le réseau. Le champ
attend une adresse qui commence par `rtsp://` ou `rtsps://`, sans espace, avec un serveur nommé.

Une adresse est proposée par défaut : remplacez-la par celle de votre caméra. Le bouton **Tester**
interroge le flux pendant quelques secondes et affiche ce qu'il reçoit :

| Ce que le test affiche | Ce que cela veut dire |
| --- | --- |
| `1920×1080 · 25 i/s · son présent` | l'image et le son arrivent : l'entrée est bonne |
| `… · sans son` | l'image arrive, mais la voix n'est pas dans le flux : vérifiez la caméra et son micro |
| `Flux injoignable : …` | l'adresse ne répond pas depuis le serveur du back-office, ou le flux est déjà pris |
| `Aucune image reçue dans le délai imparti` | le serveur ne répond pas assez vite, ou la caméra limite les connexions |
| `Le flux répond mais ne contient pas d'image` | le flux existe, mais il ne porte que du son ou des données |

Le test ne modifie rien. Il part du serveur qui héberge le back-office : une caméra sur un autre
réseau peut donc être injoignable pour le test alors qu'elle fonctionne pour l'orchestrateur, ou
l'inverse. En cas de doute, recommencez le test avant de conclure.

Une fois le test satisfaisant, cliquez sur **Continuer** : l'adresse est enregistrée et sera lue au
prochain démarrage du direct.

## 4. Brancher OBS (poussée depuis un ordinateur)

Choisissez **Poussée depuis un ordinateur** si l'image est fabriquée dans OBS. L'écran affiche une
adresse à recopier, avec un bouton **Copier** ; elle ressemble à
`rtmp://<adresse-du-serveur>:1935/live/<nom-de-la-mosquee>`. Ne la modifiez pas à la main.

| Dans OBS | Valeur |
| --- | --- |
| Service | **Personnalisé** |
| Serveur | l'adresse affichée par le back-office |
| Clé de diffusion | vide |

Le relais qui reçoit la poussée doit tourner avant le direct ; c'est lui qui récupère l'image d'OBS et
la remet à l'orchestrateur. Sans lui, OBS se connecte dans le vide et le direct n'a pas d'image.

L'adresse proposée reprend le nom par lequel vous avez ouvert le back-office. Si vous l'avez ouvert
depuis la machine elle-même, l'adresse ne sera utilisable que par OBS sur cette machine. Pour envoyer
depuis un autre ordinateur, ouvrez le back-office par son adresse réseau avant de copier l'adresse.

En mode poussée, le bouton **Tester** lit la copie du relais, pas l'adresse donnée à OBS. Il ne peut
donc rien montrer tant qu'OBS ne pousse pas : le test échoue avec `Flux injoignable`. Lancez OBS,
puis recommencez le test pour voir la résolution et le son. Le vrai contrôle reste le premier direct.

## 5. Les destinations du direct

L'écran 2 liste les adresses auxquelles le flux est envoyé. Le même flux part vers toutes les
destinations actives, dans l'ordre de la liste.

| Destination | Où prendre l'adresse RTMP |
| --- | --- |
| Page Facebook | dans l'outil de direct de la page, la clé de flux et l'adresse du serveur |
| Chaîne YouTube | dans l'outil de direct de la chaîne, l'adresse du serveur et la clé |
| Autre service | chez le fournisseur : une adresse RTMP et sa clé |

Règles de l'écran :

- **Six destinations au maximum.** Au-delà, l'enregistrement refuse la liste.
- **Un nom court** par destination, 60 caractères au plus ; sans nom, le nom du serveur est repris.
- L'adresse doit commencer par `rtmp://` ou `rtmps://`, sans espace, et contenir une clé : le dernier
  segment de l'adresse.
- La clé n'est **jamais réaffichée** après l'enregistrement. L'écran montre des points à sa place ;
  renvoyez les points tels quels pour la conserver.
- La même adresse deux fois est refusée. Une adresse vide est refusée.
- Sans destination active, le direct est simplement diffusé nulle part.

Dans la visite, les destinations sont enregistrées en une fois, au clic sur **Continuer**. Elles sont
lues au prochain démarrage du direct ; rien ne change pendant un direct en cours.

## 6. Les rediffusions, en option

Le troisième écran propose d'envoyer les enregistrements du direct vers un compartiment de stockage.
La case est **décochée par défaut** : sans elle, tout reste sur la machine de la mosquée. C'est le
choix le plus simple et il n'empêche rien.

Si vous le souhaitez, remplissez le service, le compartiment, la région, la clé d'accès et la clé
secrète, puis cochez la case. Le bouton **Tester l'écriture** dépose un petit fichier puis le retire :
il dit si le compartiment accepte vraiment les envois. Vous pouvez aussi laisser l'écran vide et
cliquer sur **Continuer** ; tout se règle plus tard dans **Réglages → Rediffusions**.

Le détail des champs et des erreurs est dans le document **Les rediffusions et leur envoi**.

## 7. Vérifier que tout est prêt, puis le premier direct

Avant de quitter la mise en route, contrôlez ces quatre points :

| Point | Ce que vous devez voir |
| --- | --- |
| Entrée vidéo | un mode choisi, une adresse enregistrée, un test qui montre une image et du son |
| Destinations | au moins une destination active, avec une adresse complète |
| Fuseau | le nom officiel de la zone de la mosquée, enregistré dans Réglages → Mosquée |
| Compartiment (facultatif) | la case cochée et le test « Écriture acceptée » |

L'écran 4 reprend ces informations en deux lignes : l'entrée et le nombre de destinations actives,
puis le compartiment s'il est réglé. **Aller au tableau de bord** termine la visite.

Au premier direct :

1. L'orchestrateur démarre et demande ses réglages au back-office : entrée vidéo, destinations,
   textes des écrans, gabarit d'écrans, compartiment.
1 bis. Si la mosquée a importé un gabarit, les écrans affichés sont **ce fichier**, rendu par le
   navigateur fourni avec l'application. Une commande l'installe sur la machine du direct :
   `python -m minbar.backoffice fetch-engine` (environ 280 Mo, aucun droit administrateur). Sans
   lui, le moteur dessine ses propres écrans : rien ne s'arrête, le rendu est simplement moins
   fidèle au fichier.
2. Si le back-office est fermé, il continue avec sa propre configuration et les notifications restent
   à blanc. Un back-office fermé ne casse jamais un direct.
3. Les écrans pleins s'affichent avant la khutba, puis l'image part vers toutes les destinations
   actives en même temps.
4. Le direct est enregistré sur la machine.
5. À l'arrêt, les enregistrements partent en tâche de fond vers le compartiment, si vous l'avez activé.
   Le traitement d'après-direct produit ensuite la transcription et les clips ; le back-office les
   importe automatiquement, en général dans la minute.

Les clips arrivent avec le statut **À valider**. Rien n'est publié sur les réseaux sans un geste
humain : la publication se fait depuis la fiche du clip, après validation.

## 8. Questions fréquentes

**J'ai quitté la mise en route au milieu, que se passe-t-il ?**
Les écrans déjà quittés sont enregistrés. La visite revient à la prochaine connexion tant qu'elle
n'a pas été terminée ou passée. Vous pouvez aussi tout reprendre dans Réglages → Direct.

**Puis-je changer de caméra après le premier direct ?**
Oui. Enregistrez la nouvelle adresse dans Réglages → Direct ; elle sera lue au prochain démarrage du
direct. On ne change pas d'entrée pendant un direct en cours.

**Le test de la caméra échoue alors que l'image arrive dans OBS.**
Le test lit le flux quelques secondes seulement, depuis le serveur du back-office. Vérifiez l'adresse,
le réseau et le nombre de connexions déjà ouvertes sur la caméra, puis recommencez. Le test ne modifie
aucun réglage.

**Faut-il refaire la visite si le nom de la mosquée change ?**
Non. Le nom et le fuseau se modifient dans Réglages → Mosquée ; la visite ne sert qu'à l'entrée, aux
destinations et au compartiment.

**Les points dans l'adresse d'une destination, c'est normal ?**
Oui. La clé de diffusion est un secret : elle n'est jamais réaffichée. Renvoyez les points pour la
conserver, ou saisissez une adresse complète pour la remplacer.
