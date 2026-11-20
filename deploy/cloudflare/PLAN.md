# Plan : console derrière le proxy Cloudflare, ingest unique sans proxy

Statut : PRÉPARÉ, RIEN D'APPLIQUÉ. Aucune de ces étapes ne s'exécute à la fusion d'une PR sauf celles
marquées « Flux » (déploiement automatique du chart/gitops). `terraform apply`, `wrangler deploy` et les
changements de DNS/pare-feu/Cloudflare sont des gestes manuels de l'opérateur, après accord du Maître.

## 1. Constat (état de prod relevé le 2026-11-20, lecture seule)

| Sujet | Constat |
|---|---|
| Ingress | k3s + Traefik 3.7 (`rancher/mirrored-library-traefik`), Ingress `sada/sada`, hôte `console.sadakhutbah.org`, 80/443, `externalTrafficPolicy: Local` (PR gitops en attente) |
| Certificat | cert-manager v1.21, `ClusterIssuer letsencrypt-prod`, **HTTP-01** via Traefik, créé par le chart (`certManager.createIssuer: true` dans la HelmRelease), Certificate `sada-tls` Ready |
| DNS | `console` : A, TTL 300, **non proxifié**, géré par Terraform (`deploy/terraform/dns.tf`, état local non versionné). Aucun enregistrement d'ingest |
| Terraform | existe : `deploy/terraform` (hcloud + cloudflare ~> 5.0), état local sur le poste de l'opérateur |
| Pare-feu Hetzner | `hcloud_firewall.sada` : 22 et 6443 `admin_cidrs` ; 80/443 `http_cidrs` (défaut ouvert) ; 1935/tcp RTMP et 8890/udp SRT `media_cidrs` (défaut ouvert) ; 8189/udp ICE WebRTC `http_cidrs` |
| Ingest | MediaMTX `receiver`, Service LoadBalancer (klipper) 1935/tcp, 8890/udp, 8189/udp sur l'IP du nœud |
| Landing | dépôt `sada-landing` : Worker `sada` (assets Astro) en domaine personnalisé sur `sadakhutbah.org` (+ `.fr` qui redirige). Pas de route de zone |
| Routes publiques | `/api/v1/public/mosques/<slug>/prayer-times[/export|/page]` (`sada/backoffice/api/public.py`), `Cache-Control: public, max-age=60, s-maxage=60, stale-while-revalidate=30` |
| Côté application | la branche locale `feat/cloudflare-proxy` (agent dev, non poussée au moment du relevé) ajoute `SADA_INGEST_HOST`, `SADA_CLOUDFLARE_PROXIES`, `SADA_PUBLIC_BASE_URL`, `SADA_ALLOWED_HOSTS/PUBLIC_HOSTS` et la lecture de `CF-Connecting-IP`. Ce plan en dépend pour l'IP visiteur et l'URL du QR du PDF |

Contrainte : un SEUL nom pour RTMP et SRT : `ingest.sadakhutbah.org`, nuage gris (le proxy Cloudflare ne
transporte pas RTMP/SRT). Ne jamais le proxifier.

## 2. Contenu des PR (toutes en brouillon)

| Dépôt | Contenu |
|---|---|
| `sada` (cette PR) | Terraform : enregistrement `ingest` (proxied=false, non variable), `console_proxied` (défaut false), `manage_zone_ssl_strict` (défaut false), `restrict_http_to_cloudflare` (défaut false, plages Cloudflare lues par `data.cloudflare_ip_ranges`, exige `console_proxied`). Chart : `certManager.solver: dns01` (Cloudflare, Secret hors git), `ingress.tls.issuer: ""` pour un certificat d'origine fourni. Valeurs CI `ci/dns01-values.yaml`, `ci/origin-cert-values.yaml`. Workflow `infra.yml`. Ce plan |
| `sada-gitops` | Traefik : `forwardedHeaders.trustedIPs` = plages Cloudflare sur `web` et `websecure`. Worker `cloudflare/worker` (`sada-public-edge`) + CI. Embarque le commit `fix(traefik): externalTrafficPolicy Local` (branche `fix/traefik-real-client-ip`, pas encore sur `main`) car les deux touchent le même fichier |
| `sada-landing` | Liaison de service `PUBLIC_EDGE` : le Worker de la landing confie `/api/v1/public/*` à `sada-public-edge`, sert le reste lui-même |

Par défaut tout est inerte : valeurs par défaut identiques à l'existant (le rendu Helm par défaut est
identique octet pour octet à `main`).

## 3. Ordre de bascule

Chaque étape a sa vérification et son retour arrière. Ne passer à la suivante que si la précédente est
verte. Ne rien faire pendant un live (vendredi, fenêtres de khutba) : voir la règle de la compétence
sada-infra (orchestrateurs STOPPED).

**0. Préparation (rien ne change en prod)**
- Fusionner (CI verte) la PR `sada` ; release-please produit la release, Flux déploie le chart. Rendu
  par défaut inchangé (HTTP-01) : vérifier `flux get helmreleases -n sada`, aucun pod hors Running.
- Fusionner la PR agent dev (`feat/cloudflare-proxy`) avant l'étape 5 (IP visiteur, URL du QR).

**1. Traefik fait confiance à Cloudflare** (PR gitops, Flux)
- Effet : redémarrage de Traefik par k3s (coupure de quelques secondes sur 80/443 ; 1935/8890 ne passent
  pas par Traefik). Inerte pour le trafic actuel (aucun visiteur direct n'a une IP Cloudflare).
- Vérifier : `kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik` ; arguments contenant
  `forwardedHeaders.trustedIPs` ; `curl -I https://console.sadakhutbah.org` OK.
- Retour arrière : revert du commit dans `sada-gitops`.

**2. Jeton Cloudflare pour DNS-01** (manuel, Maître)
- Créer un jeton API distinct de celui de Terraform : permission Zone:DNS:Edit, ressource = zone
  `sadakhutbah.org` seule, filtre IP = IP du serveur ou aucun (cert-manager l'appelle depuis le cluster ;
  le jeton Terraform est filtré sur le poste de l'opérateur et ne convient pas).
- Le Secret va dans le namespace `cert-manager`, créé à la main, jamais dans git :
  `kubectl -n cert-manager create secret generic cloudflare-api-token --from-file=api-token=<fichier>`
  (le jeton est lu depuis un fichier, pas tapé en clair dans l'historique du shell).
- Retour arrière : supprimer le Secret.

**3. Émission/renouvellement en DNS-01** (PR gitops à écrire à ce moment : valeurs de la HelmRelease)
- Valeurs : `certManager.solver: dns01`, `certManager.dns01.cloudflare.apiTokenSecretName:
  cloudflare-api-token`, `certManager.dns01.dnsZones: ["sadakhutbah.org"]`. Flux met à jour le
  ClusterIssuer en place (même nom `letsencrypt-prod`, le Certificate n'est pas touché).
- Essai à blanc d'abord avec le serveur de test (`certManager.server:
  https://acme-staging-v02.api.letsencrypt.org/directory` sur un Certificate jetable) pour ne pas
  consommer les limites de Let's Encrypt, puis retour au serveur de production.
- Forcer un renouvellement : `cmctl renew -n sada sada-tls`, puis vérifier
  `kubectl -n sada describe certificate sada-tls` (événement « Issuing » puis Ready) et que le Challenge
  est de type DNS-01 (`kubectl get challenges -A`). Console toujours non proxifiée : sans risque.
- Retour arrière : `certManager.solver: http01` (revert du commit gitops).
- Variante sans cert-manager : certificat d'origine Cloudflare (Origin CA, 15 ans), voir §5.

**4. Terraform : ingest (+ option SSL strict)** (manuel, opérateur)
- `terraform plan` : attendu = `+ cloudflare_dns_record.ingest[0]` seul (A `ingest`, proxied false). Le
  record `console` ne doit pas changer (`proxied=false`, TTL 300). Si un autre changement apparaît,
  s'arrêter. Puis `terraform apply` après accord.
- Vérifier : `dig +short ingest.sadakhutbah.org` = IP du serveur ; `nc -vz ingest.sadakhutbah.org 1935` ;
  un push OBS de test (RTMP puis SRT) sur le slug `test` : `rtmp://ingest.sadakhutbah.org:1935/live/test`.
- Communiquer le nouveau nom aux mosquées ; l'ancien (IP brute ou `console.…:1935`) continue de répondre
  tant que le pare-feu n'a pas changé. Ne pas retirer l'ancien accès avant que toutes aient migré.
- `manage_zone_ssl_strict = true` (Full strict) : avant de proxifier. Sans effet sur un enregistrement
  gris. Il s'applique à toute la zone, landing comprise (Worker hébergé chez Cloudflare : pas
  d'origine à valider, sans risque attendu) ; vérifier `https://sadakhutbah.org` juste après.
- Retour arrière : `terraform apply` avec la variable remise à false ; l'enregistrement ingest peut
  rester.

**5. Worker public** (manuel, opérateur)
- Dans `sada-gitops/cloudflare/worker` : `npm ci && npm test`, `wrangler deploy` du Worker
  `sada-public-edge` (aucune route : il n'est joignable que par liaison de service).
- Test direct : `wrangler dev` ou l'URL préproduction, puis fusionner la PR `sada-landing` (la liaison exige
  que le Worker existe, sinon le déploiement de la landing échoue). Workers Builds déploie la landing.
- Vérifier : `curl -sI https://sadakhutbah.org/api/v1/public/mosques/exemple/prayer-times` (slug réel de
  test) : `x-sada-edge: MISS` puis `HIT`, `cache-control` public ; `/` toujours la landing.
- Réglage app : `backoffice.publicBaseUrl: https://sadakhutbah.org` (QR du PDF) via la HelmRelease ;
  si `allowedHosts` est utilisé, inclure aussi `backoffice` et `backoffice.sada.svc`.
- Retour arrière : revert de la PR landing (le Worker `sada-public-edge` peut rester inutilisé).

**6. Console derrière le proxy** (manuel, opérateur, hors live)
- Pré-requis : étapes 1 à 4 vérifiées ; certificat renouvelé en DNS-01 ; mosquées migrées vers `ingest`
  si elles poussent encore vers `console` ; DNS-01 testé.
- `terraform apply -var console_proxied=true` : plan attendu = `console` en `proxied: true`, TTL 1.
- Vérifier (dans les 5 minutes, TTL précédent 300 s) : `curl -sI https://console.sadakhutbah.org` avec
  en-têtes `cf-ray` et `server: cloudflare` ; connexion au back office ; moniteurs de la régie (WebRTC +
  signalisation) ; l'IP visiteur vue par l'application (journaux : limite de requêtes par visiteur, pas
  par IP Cloudflare) ; téléversements et exports PDF.
- Retour arrière immédiat : `terraform apply -var console_proxied=false` (ou nuage gris dans le tableau de
  bord Cloudflare : plus rapide), propagation ≤ 5 min. Le certificat n'est pas touché.

**7. Option : pare-feu limité à Cloudflare** (manuel, ≥ 48 h après l'étape 6)
- `restrict_http_to_cloudflare = true` (exige `console_proxied`) : 80/443 limités aux plages Cloudflare
  (22 CIDR, sous la limite Hetzner de 500 sources par règle). Plus d'accès direct à l'IP en HTTP(S) ;
  1935/tcp, 8890/udp et 8189/udp (WebRTC) restent régis par `media_cidrs`/`http_cidrs`, SSH/6443 par
  `admin_cidrs`.
- Avec ce réglage, HTTP-01 ne fonctionne plus (c'est le but de l'étape 3) ; le renouvellement DNS-01 ne
  dépend pas du pare-feu.
- Retour arrière : variable à false puis `terraform apply` (si SSH/k3s inaccessibles : console Hetzner,
  règle de pare-feu à la main).

## 4. Risques

- **Renouvellement de certificat** : l'ordre compte. Proxifier avant DNS-01 fait échouer HTTP-01 au
  prochain renouvellement (≈ 30 j avant l'échéance) : panne différée et silencieuse. Les étapes 3 puis 6.
- **Vidéo et médias via le proxy** : l'offre gratuite de Cloudflare limite le corps à 100 Mo et ferme une
  requête sans réponse après 100 s (HTTP 524) ; et ses conditions déconseillent de servir de gros
  volumes de vidéo non HTML par le proxy. Vérifier ce qui passe par `console` : téléversements
  d'enregistrements/clips, téléchargements d'exports, HLS des moniteurs de la régie. Décision nécessaire
  (§6).
- **WebSocket / SSE** : pris en charge par Cloudflare, mais coupés après des minutes d'inactivité côté
  edge sur certains plans ; tester les moniteurs en direct.
- **IP visiteur** : Traefik ne conserve `X-Forwarded-*` que depuis les plages de confiance ; une plage
  Cloudflare nouvelle non listée fait voir l'IP de l'edge (limite de requêtes partagée, 60/min pour tous).
- **Plages Cloudflare** : trois copies à tenir (Traefik gitops, code de l'application, pare-feu). Le
  pare-feu se met à jour tout seul (`data.cloudflare_ip_ranges`) au prochain `terraform apply` ; Traefik
  et l'application : revoir `https://www.cloudflare.com/ips/` une fois par trimestre (etag relevé :
  `38f79d05…`).
- **Full (strict) sur la zone** : concerne aussi `sadakhutbah.fr` si sa zone est la même ; elle n'est pas
  gérée ici.
- **Ingest** : nom jamais proxifié (pas de variable pour cela) ; un nuage orange sur `ingest` couperait
  RTMP/SRT. Le pare-feu ne filtre pas par Cloudflare pour 1935/8890.
- **Fusion** : la PR gitops met Traefik à jour dès la fusion (Flux). Les PR Terraform, Worker et landing
  n'ont aucun effet de production tant que les gestes manuels ne sont pas faits ; la PR landing, elle,
  déclenche un déploiement Workers Builds à la fusion sur `main`.
- **Jeton** : le jeton DNS-01 donne l'édition DNS de la zone ; Secret du cluster uniquement, portée
  minimale, à révoquer si compromis. Aucun secret n'est versionné dans ces PR.

## 5. Option : certificat d'origine Cloudflare (sans DNS-01)

Alternative si on ne veut pas de jeton Cloudflare dans le cluster : générer un certificat d'origine (SSL/TLS
> Origin Server) pour `console.sadakhutbah.org`, le placer dans un Secret `kubernetes.io/tls`
(`console-origin-cert`, créé à la main) et rendre le chart avec `ingress.tls.issuer: ""` et
`ingress.tls.secretName: console-origin-cert` (valeurs de test : `ci/origin-cert-values.yaml`). Seul
Cloudflare fait confiance à ce certificat : l'accès direct (nuage gris, retour arrière) affichera une erreur
de certificat. Renouvellement manuel avant l'échéance choisie (jusqu'à 15 ans). Moins robuste pour le
retour arrière que DNS-01.

## 6. Décisions à prendre (Maître)

1. DNS-01 (recommandé : automatique, retour arrière propre) ou certificat d'origine Cloudflare.
2. Médias et téléversements lourds via `console` proxifié : accepter les limites (100 Mo, 100 s) et les
   conditions Cloudflare, ou garder un nom séparé non proxifié (gris) pour les flux lourds.
3. Faut-il activer `manage_zone_ssl_strict` sur toute la zone (landing comprise) ?
4. Étape 7 (pare-feu limité à Cloudflare) : souhaitée ou on garde 80/443 ouverts ?
5. Slug de test à utiliser pour valider (`test` existe en prod) et fenêtre de bascule hors vendredi.
6. Garder l'ancien accès RTMP/SRT par IP pendant combien de temps après migration vers `ingest` ?

## 7. Maintenance

- Plages Cloudflare : `curl -s https://api.cloudflare.com/client/v4/ips` ; comparer à
  `clusters/prod/kube-system/traefik-config.yaml` (gitops) et à `CLOUDFLARE_RANGES` (application).
- Contrôle du plan Terraform : toujours lire le plan ; il ne doit montrer que les changements prévus.
