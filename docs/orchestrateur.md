# L'orchestrateur et le jeton interne

Deux programmes tournent côte à côte sur la même machine. Ce document explique ce que fait chacun,
pourquoi ils se parlent, et à quoi sert le fameux **jeton interne** — le fichier `internal.token`
dont personne ne se souvient.

---

## 1. Les deux programmes

| Programme | Ce qu'il fait | Qui le voit |
| --- | --- | --- |
| **Le back-office** | l'interface dans le navigateur : réglages, khutbas, clips, journal, écrans | l'administrateur, avec son compte et son mot de passe |
| **L'orchestrateur** | le direct lui-même : il ouvre la caméra, encode, pousse vers les plateformes, écoute la voix, coupe, poste la rediffusion | personne : il n'a pas d'écran |

Le back-office est un serveur web. L'orchestrateur est un programme en ligne de commande.
Ils ne partagent **pas de mémoire**, et pas non plus de base de données : l'orchestrateur n'ouvre
jamais le fichier SQLite du back-office.

## 2. Pourquoi l'orchestrateur a besoin de lui parler

Certains réglages saisis dans le navigateur ne peuvent pas être connus avant le démarrage du
direct : l'adresse où poster une notification de fin, les messages à envoyer, les textes des écrans,
les destinations du flux. L'orchestrateur les demande donc **au moment de démarrer**, par un appel
HTTP local, à la seule source qui fait autorité : le back-office.

Concrètement, au démarrage, l'orchestrateur interroge :

- `/api/v1/internal/notification-settings` — où prévenir quand une khutba est publiée ;
- `/api/v1/internal/live-targets` — les destinations du direct ;
- `/api/v1/internal/screens` — les textes des écrans pleins ;
- `/api/v1/internal/live-input` — la caméra ou la poussée OBS à lire ;
- `/api/v1/internal/storage` — l'envoi facultatif des rediffusions dans un bucket.

Si le back-office ne répond pas, l'orchestrateur ne s'arrête pas : il continue avec ce qu'il a dans
son fichier de configuration, et les notifications restent à blanc. Un back-office fermé ne casse
jamais un direct.

## 3. Deux jetons, à ne pas confondre

Le mot « jeton » apparaît deux fois dans le projet, et ils vont **dans des directions opposées** :

| Jeton | Qui l'envoie | À qui | Où on le saisit |
| --- | --- | --- | --- |
| **jeton interne** (`internal.token`) | l'orchestrateur | le back-office | nulle part : fichier local, créé tout seul |
| **jeton de commande** (`orchestrator_token`) | le back-office | l'orchestrateur | Réglages → Mosquée, et `--api-token` à la main |

- Le **jeton interne** protège les adresses que l'orchestrateur interroge pour *lire* ses réglages.
  Il existe toujours, il est automatique, vous n'avez rien à faire.
- Le **jeton de commande** protège les adresses que le back-office appelle pour *piloter* le direct :
  démarrer, arrêter, prolonger, changer de scène. Il est **facultatif** : laissé vide, l'orchestrateur
  accepte les commandes de n'importe quel programme de la machine. Renseigné, il exige l'en-tête
  `X-Token`, et il faut donner la **même valeur** des deux côtés — le champ du back-office et
  l'option `--api-token` au lancement de l'orchestrateur.

Si les boutons du tableau de bord répondent « orchestrateur injoignable » alors qu'il tourne, c'est
presque toujours ce jeton de commande : renseigné d'un côté, différent ou absent de l'autre. Videz le
champ et relancez les deux pour retrouver un état simple.

## 4. Le problème que le jeton interne résout

Ces adresses sont sur `127.0.0.1`, mais « local » ne veut pas dire « privé ». Sur la même machine,
n'importe quel programme tourne avec les mêmes droits : un onglet ouvert sur un site malveillant
peut appeler `http://127.0.0.1:8801/api/v1/internal/screens` et lire les réglages de la mosquée ;
un autre logiciel installé peut faire de même. Une session d'administrateur ne marche pas ici : un
programme n'a pas de cookies, pas d'écran de connexion, pas de CSRF.

D'où le jeton : **un secret local partagé**, que seule une machine — et en pratique seul un
utilisateur — peut lire.

- il est créé au premier besoin, par le premier des deux programmes qui démarre ;
- il vit dans `data/internal.token`, en permission `0600` (lisible par son propriétaire seulement) ;
- il est tiré au hasard (`secrets.token_urlsafe`), 43 caractères, jamais affiché dans une interface ;
- il s'envoie dans un en-tête `X-Internal-Token`, comparé en temps constant (pas de fuite par le
  temps de réponse) ;
- sans lui, ces adresses répondent `403 jeton interne requis`.

C'est un **cadenas de porte intérieure**, pas une serrure de coffre : il empêche qu'un autre
programme de la machine se serve dans les réglages. Il ne remplace ni le mot de passe de
l'administrateur, ni le chiffrement du réseau.

## 5. Ce qu'il ne faut pas faire

- **Ne pas publier ce fichier.** Il ne va pas dans le dépôt (le dossier `data/` est ignoré) et ne
  s'envoie pas par messagerie.
- **Ne pas le mettre dans un réglage du back-office.** Il n'est pas modifiable depuis l'interface,
  c'est voulu : une interface ne distribue pas de secret.
- **Ne pas le copier sur une autre machine.** Si l'orchestrateur tourne ailleurs que le back-office,
  il faut alors un vrai réseau privé (VPN, Tailscale) et non un jeton recopié.

Si vous le supprimez, rien ne casse : le fichier est recréé au démarrage suivant des deux
programmes, avec une nouvelle valeur. Faites-le redémarrer tous les deux, sinon l'un présentera
l'ancien jeton et recevra un refus jusqu'à son propre redémarrage.

## 6. Mise en route, dans l'ordre

1. **Remplir les réglages** dans le back-office : mosquée, caméra ou poussée OBS, destinations,
   notifications, textes des écrans. C'est ce que l'orchestrateur lira.
2. **Laisser le back-office tourner** : le glisser dans un terminal dédié, ou en service.
3. **Lancer l'orchestrateur** dans un second terminal : il demande ses réglages au back-office et
   affiche dans son journal ce qu'il a reçu, ou la raison pour laquelle il n'a rien reçu.
4. **Vérifier** que le jeton existe et n'est lisible que par vous :

```bash
ls -l data/internal.token      # -rw------- attendu
```

## 7. Questions fréquentes

**Faut-il saisir le jeton quelque part ?**
Non. Les deux programmes le lisent dans le même fichier. Il n'est jamais tapé à la main.

**L'interface me demande un jeton, c'est grave ?**
Deux cas. Sur une adresse `/internal/...`, c'est un appel direct depuis un navigateur : pas un
chemin prévu, les réglages passent par `/api/v1/settings`. Sur les boutons du tableau de bord, voir
la section 3 : c'est le jeton de commande qui ne correspond pas.

**Peut-on s'en passer ?**
Oui, en faisant tourner les deux programmes dans un même processus — mais alors le back-office
tombe avec le direct, et une erreur d'interface coupe l'antenne. La séparation coûte ce jeton ;
elle vaut ce prix.

**Et si je change de machine ?**
Vous emportez `data/` (base et jeton) ou vous laissez les deux programmes recréer un jeton neuf sur
la nouvelle machine.
