# Runbook : renommage de l'organisation GitHub `sadakhutbah` en `thesadaproject`

Objectif : zéro coupure en production, aucun poste Studio perdu. Ce document est sur la branche
`chore/rename-org` des dépôts `sada` (avec ce runbook), `sada-studio` et `sada-gitops` ; les trois
branches sont poussées, sans PR, et ne touchent ni `main` ni le cluster.

Le DOMAINE ne change pas : `sadakhutbah.org`, `console.sadakhutbah.org`, `sadakhutbah.fr` restent tels quels.
L'`appId` de Studio (`org.sadakhutbah.studio`) ne change PAS non plus : l'identifiant de l'application pour
l'OS et pour les mises à jour. Le changer créerait une seconde application (doublon, plus de mise à jour).

## 0. Ce qui se passe au renommage (faits à connaître)

| Élément | Après renommage |
| --- | --- |
| Pages web et `git clone/fetch/push` sur l'ancien nom | redirigés par GitHub |
| API REST (`gh api repos/sadakhutbah/...`), page d'organisation | 404 sur l'ancien nom |
| Images `ghcr.io/sadakhutbah/sada` | NON redirigées : le chemin ancien peut cesser de répondre |
| Application GitHub `sada-flux` (Flux), secrets Actions, webhooks, règles de branche | suivent l'organisation (identifiants numériques inchangés) |
| Redirections de l'ancien nom | valables tant que personne ne recrée un compte/une organisation `sadakhutbah` |

**Ne jamais recréer ni laisser prendre `sadakhutbah`** : cela casserait les redirections dont dépendent
les Studio 0.1.0 déjà installés (leur flux de mise à jour est `github.com/sadakhutbah/sada-studio/releases`,
écrit en dur dans l'application installée) et l'ancienne URL Flux.

### État constaté avant renommage (21/11/2026)

- Flux : `GitRepository flux-system` = `https://github.com/sadakhutbah/sada-gitops`, `provider: github`, secret
  `flux-github-app` (clés `githubAppID`, `githubAppInstallationID`, `githubAppPrivateKey` : application GitHub
  `sada-flux`, pas de clé de déploiement SSH). `GitRepository sada` = `https://github.com/sadakhutbah/sada`
  (public, sans secret). `ImageRepository sada` = `ghcr.io/sadakhutbah/sada` (anonyme, sans secret ; 143 tags).
  `ImagePolicy sada` = semver. `ImageUpdateAutomation sada` pousse sur `main` de `sada-gitops` via l'application.
- Cluster : aucun `imagePullSecrets` (chart `imagePullSecrets: []`) ; le kubelet/containerd tire l'image en
  anonyme (paquet public). Image en prod : `ghcr.io/sadakhutbah/sada:0.1.0-alpha.13`, digest de l'index
  `sha256:bba6559e51b52fa18092ac770ce0ba83d4c075f3f9e8b550404eede1a1a2f588`, `pullPolicy: IfNotPresent`.
- **Tous les Deployments sont en stratégie `Recreate`** (backoffice, metrics-sampler, `orchestrator-<slug>`,
  receiver). Changer le nom d'image dans la HelmRelease tue le pod avant de démarrer le nouveau : si l'image
  n'est pas tirable sous le nouveau nom, backoffice et orchestrateurs tombent. D'où l'étape 4 (pré-tirage).
- Le receiver (`bluenviron/mediamtx:1`) et PostgreSQL (CNPG) ne dépendent pas de ce nom d'image.
- Réf. `thesadaproject` : le nom était libre (404 sur `users/` et `orgs/`) le 21/11/2026. GitHub refusera
  le renommage s'il a été pris entre-temps.

## 1. Avant de renommer (Raphaël, interface web)

1. Choisir un créneau HORS vendredi et hors tout live. Vérifier les orchestrateurs :
   `kubectl -n sada logs deploy/orchestrator-<slug> --tail=5` doit montrer STOPPED (voir skill `sada-infra`).
2. Renseigner `gh auth refresh -h github.com -s read:packages,write:packages` (le jeton actuel n'a pas
   ces portées ; nécessaire pour vérifier/copier l'image). Ne rien afficher du jeton.
3. Relever l'état de référence : `kubectl -n sada get pods,ingress,certificate` et
   `flux get sources git -A; flux get images all -A; flux get helmreleases -n sada` (tout `True`).
4. Renommer : Organisation > Settings > Organization name > `thesadaproject`.

À partir de là, rien n'est modifié sur le cluster ; l'ancienne URL git continue de répondre (redirection),
Flux continue de tourner tel quel.

## 2. Vérifications immédiates après renommage

```bash
gh api orgs/thesadaproject --jq .login                        # attendu: thesadaproject
gh api repos/thesadaproject/{sada,sada-studio,sada-gitops,sada-landing} --jq .full_name
gh api repos/sadakhutbah/sada 2>&1 | head -1                  # attendu: 404 (normal)
git ls-remote https://github.com/sadakhutbah/sada.git HEAD    # attendu: répond (redirection git)
curl -sIL -o /dev/null -w '%{http_code} %{url_effective}\n' https://github.com/sadakhutbah/sada-studio/releases.atom
gh api orgs/thesadaproject/installations --jq '.installations[]|.app_slug'  # sada-flux doit y rester
```

Contrôle Flux : `flux get sources git -A` doit rester `True` (via redirection). Si `flux-system` passe en
erreur, passer à l'étape 6 plus tôt.

## 3. Postes locaux

```bash
cd ~/dev/sada          && git remote set-url origin https://github.com/thesadaproject/sada.git
cd ~/dev/sada-studio   && git remote set-url origin https://github.com/thesadaproject/sada-studio.git
cd ~/dev/sada-gitops   && git remote set-url origin https://github.com/thesadaproject/sada-gitops.git
cd ~/dev/sada-landing-wd && git remote set-url origin https://github.com/thesadaproject/sada-landing.git
for d in sada sada-studio sada-gitops sada-landing-wd; do git -C ~/dev/$d fetch origin && git -C ~/dev/$d remote -v | head -1; done
```
Idem sur tout autre clone (Mac, poste Studio de test).

## 4. Image : existence sous le nouveau nom, visibilité, pré-tirage (OBLIGATOIRE avant la PR gitops)

```bash
OLD=ghcr.io/sadakhutbah/sada:0.1.0-alpha.13
NEW=ghcr.io/thesadaproject/sada:0.1.0-alpha.13
DIGEST=sha256:bba6559e51b52fa18092ac770ce0ba83d4c075f3f9e8b550404eede1a1a2f588

# a) nouveau chemin, accès ANONYME (comme le kubelet)
TOK=$(curl -s "https://ghcr.io/token?scope=repository:thesadaproject/sada:pull" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -sI -H "Authorization: Bearer $TOK" \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  https://ghcr.io/v2/thesadaproject/sada/manifests/0.1.0-alpha.13 | grep -i -E '^HTTP|docker-content-digest'
# attendu: 200 et le digest ci-dessus.  (docker manifest inspect $NEW donne aussi le résultat)

# b) ancien chemin (information)
docker manifest inspect $OLD >/dev/null && echo "ancien chemin: répond" || echo "ancien chemin: ne répond plus"
```

- **Cas 1 : 200 + même digest** : les paquets ont suivi l'organisation. Vérifier la visibilité du paquet :
  github.com/orgs/thesadaproject/packages > `sada` > Package settings : Visibility = Public (sinon le kubelet,
  sans pull secret, aurait 401). Vérifier aussi « Manage Actions access » : le dépôt `sada` doit avoir Write
  (sinon la CI ne pourra plus pousser, 403).
- **Cas 2 : 404/403 (paquet absent ou privé)** : recopier toutes les versions publiées (tags semver) avec
  conservation du digest, avec un jeton ayant `write:packages` (ne jamais l'afficher) :
  ```bash
  gh auth token | docker login ghcr.io -u "$(gh api user --jq .login)" --password-stdin
  # crane est préférable (copie l'index multi-arch tel quel, même digest) :
  #   go install github.com/google/go-containerregistry/cmd/crane@latest
  crane copy ghcr.io/sadakhutbah/sada:0.1.0-alpha.13 ghcr.io/thesadaproject/sada:0.1.0-alpha.13
  # sans crane :  docker buildx imagetools create -t $NEW $OLD   (même contenu, digest de l'index identique)
  # (optionnel) autres tags : for t in $(crane ls ghcr.io/sadakhutbah/sada | grep -E '^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$'); do crane copy ...:$t ...:$t; done
  ```
  Puis rendre le paquet public (Package settings > Change visibility > Public), lier le dépôt `sada`
  (Connect repository) et lui donner Write dans « Manage Actions access ». Refaire le contrôle a) : même digest.
  Si l'ancien chemin répond encore, le laisser en place (retour arrière possible).
- **Pré-tirage sur le nœud** (supprime tout risque de pull au moment du `Recreate`), sans toucher aux pods :
  ```bash
  ssh -i ~/.config/sada-deploy/id_ed25519 -o IdentitiesOnly=yes root@178.105.12.4 \
    'k3s crictl pull ghcr.io/thesadaproject/sada:0.1.0-alpha.13 && k3s crictl images | grep thesadaproject'
  ```
  Attendu : référence présente avec le même digest. À partir de là `IfNotPresent` suffit même si ghcr est injoignable.

## 5. Fusion de la PR `sada` (chore/rename-org) : sans effet sur le cluster

1. Ouvrir la PR (depuis `thesadaproject/sada`), attendre la CI VERTE (règle du Maître ; « no checks » n'est pas vert).
2. Fusionner. La CI construit `main-<run>-<sha>` sous `ghcr.io/thesadaproject/sada` (`github.repository`), sans déploiement.
   Vérifier que le push réussit (sinon : « Manage Actions access » du paquet, étape 4).
3. Effet Flux : `GitRepository sada` change de révision ; le chart ne change que par la valeur par défaut
   `image.repository` que la HelmRelease surcharge : aucun rendu différent, aucun redémarrage.
   Contrôle : `kubectl -n sada get pods` (âges inchangés), `flux get helmreleases -n sada`.
4. Les PR release-please suivantes référenceront `thesadaproject` automatiquement. L'historique
   `CHANGELOG.md` garde les anciens liens (redirigés), volontairement non réécrits.

## 6. PR `sada-gitops` : la seule qui touche le cluster

Contenu : `gotk-sync.yaml` (url de `flux-system`), `source.yaml` (GitRepository `sada`), `image-policy.yaml`
(ImageRepository/ImagePolicy), `helmrelease.yaml` (`image.repository`), commentaires.

Pré-requis : étapes 2 et 4 faites, orchestrateurs STOPPED, pas un vendredi.

1. Ouvrir la PR, CI verte, fusionner. Flux applique :
   - `flux-system` GitRepository vers `https://github.com/thesadaproject/sada-gitops` (l'application `sada-flux`
     s'authentifie par `githubAppInstallationID`, inchangé ; pas de clé de déploiement à recréer) ;
   - `GitRepository sada`, `ImageRepository sada` (nouveau chemin), `ImagePolicy sada` ;
   - HelmRelease : `image.repository` change, donc **redémarrage `Recreate`** de backoffice, metrics-sampler
     et orchestrateurs (coupure de quelques secondes à dizaines de secondes ; le receiver n'est pas touché).
2. Forcer et suivre :
   ```bash
   export KUBECONFIG=~/.config/sada-deploy/kubeconfig
   flux reconcile source git flux-system && flux reconcile kustomization flux-system --with-source
   flux get sources git -A                       # url thesadaproject/*, Ready True
   flux get images all -A                        # ImageRepository: scan OK sur ghcr.io/thesadaproject/sada ; policy 0.1.0-alpha.13
   flux get helmreleases -n sada                 # Ready, révision +1
   kubectl -n sada get pods,ingress,certificate  # tout Running, certificat Ready
   kubectl -n sada get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.containers[0].image}{"\n"}{end}'
   curl -sI https://console.sadakhutbah.org/ | head -1
   ```
3. Si `flux-system` ne se met pas à jour sur la nouvelle URL (erreur d'authentification) : pas de coupure
   (le dernier artefact reste appliqué). Corriger à la main :
   `kubectl -n flux-system patch gitrepository flux-system --type=merge -p '{"spec":{"url":"https://github.com/thesadaproject/sada-gitops"}}'`
   (si l'application `sada-flux` a perdu l'accès : Org > Settings > GitHub Apps > sada-flux > Configure > dépôts).
4. Vérifier que `ImageUpdateAutomation` pousse toujours : à la prochaine release (étape 5.4), un commit
   `sada: deploy 0.1.0-alpha.N` par `fluxcdbot` doit arriver sur `main` de `sada-gitops`.

## 7. Studio : version 0.1.1 et tag

La branche `chore/rename-org` de `sada-studio` contient : `electron-builder.yml` `publish.owner: thesadaproject`,
`package.json`/`package-lock.json` en `0.1.1`, README et badges. `appId` inchangé.

Principe : les Studio 0.1.0 interrogent encore `sadakhutbah/sada-studio` (redirigé) : ils téléchargent 0.1.1,
dont l'`app-update.yml` embarqué pointe sur `thesadaproject/sada-studio` : migration définitive.

1. Ouvrir la PR, CI verte, fusionner.
2. Tag (déclenche build des 3 plateformes puis la Release publique) :
   ```bash
   cd ~/dev/sada-studio && git checkout main && git pull
   git tag -a v0.1.1 -m "v0.1.1" && git push origin v0.1.1
   gh run watch -R thesadaproject/sada-studio
   ```
3. Contrôler la Release : `gh release view v0.1.1 -R thesadaproject/sada-studio --json assets --jq '.assets[].name'`
   doit contenir `latest.yml` (Windows), `latest-mac.yml`, `latest-linux.yml`, les installateurs et `.blockmap`.
4. Contrôler le chemin des 0.1.0 (ancien nom, via redirection) :
   ```bash
   curl -sIL -o /dev/null -w '%{http_code}\n' https://github.com/sadakhutbah/sada-studio/releases/latest/download/latest.yml   # 200
   curl -sL https://github.com/sadakhutbah/sada-studio/releases/latest/download/latest.yml | head -3                         # version: 0.1.1
   ```
5. Sur un poste Studio 0.1.0 de test : forcer la vérification de mise à jour, constater 0.1.1, puis
   que l'application mise à jour interroge `thesadaproject/sada-studio` (journal de l'updater).
6. Ne PAS supprimer la release v0.1.0 ni renommer ses fichiers. macOS non signé ne se met pas à jour
   automatiquement (limite déjà connue) : ces postes doivent réinstaller 0.1.1 à la main.

## 8. Autres points à contrôler

- `sada-landing` : aucune référence au nom d'organisation (le domaine `sadakhutbah.fr` reste). L'application
  GitHub « Cloudflare Workers and Pages » est installée sur l'organisation : dans le tableau de bord Cloudflare,
  vérifier que la connexion Git (Workers Builds) de la landing et du worker reste valide et relancer un build.
- Connaissance locale à mettre à jour après coup : skill `sada-infra`, savoir partagé, SOUL de `daikenja-dev`.
- Secrets Actions, règles de protection de branche, release-please (`GITHUB_TOKEN` ou PAT) : vérifier qu'une
  PR de release s'ouvre et que ses checks se lancent.

## 9. Retour arrière

- Avant l'étape 6 (gitops) : rien n'a changé sur le cluster. Annuler = renommer l'organisation en `sadakhutbah`
  (possible si le nom n'est pas pris ; les paquets reprennent le chemin d'origine) ; remotes locaux : `git remote set-url`
  vers l'ancien nom.
- Après l'étape 6 : `git revert -m 1 <merge de la PR gitops>` sur `main` de `sada-gitops` (PR de revert) restaure
  URL et image ; l'ancien chemin d'image ne répond peut-être plus : l'image sous l'ancien nom est alors
  celle du cache containerd (`IfNotPresent`), ou recopier `ghcr.io/thesadaproject/sada` vers l'ancien nom
  seulement si le renommage est défait. Urgence : `flux suspend helmrelease sada -n sada` puis
  `helm -n sada rollback sada <rev>` ; le cache du nœud contient les deux références (étape 4).
- Ne jamais supprimer la HelmRelease ni le namespace (le PVC de données serait détruit).
- Studio : si 0.1.1 est défectueux, publier 0.1.2 corrigée (ne pas supprimer 0.1.1 : les clients passeraient en erreur
  de manifeste), la redirection de l'ancien nom reste le seul lien des 0.1.0.
