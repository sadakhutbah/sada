#!/usr/bin/env bash
# Bring Sadā up on one Hetzner server: Terraform (machine, firewall, volume), Ansible (k3s),
# Helm (cert-manager, CloudNativePG, Sadā). Every step is idempotent; run them one by one the
# first time, or `all` when you know what you are doing.
#
#   ./deploy/up.sh plan      # terraform init + plan (no resource created)
#   ./deploy/up.sh infra     # terraform apply: server, firewall, SSH key, volume (BILLABLE)
#   ./deploy/up.sh cluster   # ansible: mount the volume, install k3s, fetch the kubeconfig
#   ./deploy/up.sh deps      # helm: cert-manager + CloudNativePG
#   ./deploy/up.sh image     # build and push the image by hand (the CI on main normally does it)
#   ./deploy/up.sh app       # helm upgrade --install sada (fresh cluster / disaster recovery only:
#                            # production is deployed by Flux from thesadaproject/sada-gitops)
#   ./deploy/up.sh status    # pods, services, ingress, certificates
#
# Configuration: environment variables, or deploy/deploy.env (git-ignored), e.g.
#   SADA_ADMIN_CIDRS="203.0.113.10/32"      # mandatory for `infra`
#   SADA_IMAGE_REPO="ghcr.io/you/sada"
#   SADA_IMAGE_TAG="sha-$(git rev-parse HEAD)"   # default: the image the CI built for HEAD
#   SADA_ACME_EMAIL="you@example.org"       # creates the Let's Encrypt ClusterIssuer
#   SADA_HOST="console.sadakhutbah.org"
#   SADA_VALUES="deploy/values.local.yaml"  # your mosques: [...]
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEPLOY_DIR="$REPO_ROOT/deploy"
TF_DIR="$DEPLOY_DIR/terraform"
ANS_DIR="$DEPLOY_DIR/ansible"
CHART="$DEPLOY_DIR/helm/sada"

# shellcheck disable=SC1091
[ -f "$DEPLOY_DIR/deploy.env" ] && set -a && . "$DEPLOY_DIR/deploy.env" && set +a

SADA_NAMESPACE=${SADA_NAMESPACE:-sada}
SADA_RELEASE=${SADA_RELEASE:-sada}
SADA_HOST=${SADA_HOST:-console.sadakhutbah.org}
SADA_IMAGE_REPO=${SADA_IMAGE_REPO:-ghcr.io/thesadaproject/sada}
# The CI builds and publishes the image on every push to main (tag sha-<full commit>): deploy that.
SADA_IMAGE_TAG=${SADA_IMAGE_TAG:-sha-$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo local)}
SADA_VALUES=${SADA_VALUES:-$DEPLOY_DIR/values.local.yaml}
KUBECONFIG=${KUBECONFIG:-$HOME/.config/sada-deploy/kubeconfig}
export KUBECONFIG

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed (see deploy/README.md)"; }

# API tokens never go on the command line: load them from their file into TF_VAR_*.
load_token() {
  local var="$1" file="$2"
  [ -n "${!var:-}" ] && return 0
  [ -f "$file" ] || die "no $var: write $file or export $var"
  printf -v "$var" '%s' "$(cat "$file")"
  export "${var}"
}

# Hetzner (machine) and Cloudflare (console DNS record).
tokens() {
  load_token TF_VAR_hcloud_token "$HOME/.config/sada-deploy/hcloud_token"
  load_token TF_VAR_cloudflare_api_token "$HOME/.config/sada-deploy/cloudflare_token"
}

confirm() {
  [ "${SADA_YES:-}" = "1" ] && return 0
  printf '%s\n' "$1"
  read -r -p "Continue? [y/N] " answer
  [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "aborted"
}

cmd_plan() {
  need terraform; tokens
  local vars=()
  [ -n "${SADA_ADMIN_CIDRS:-}" ] && vars+=(-var "admin_cidrs=[\"${SADA_ADMIN_CIDRS//,/\",\"}\"]")
  cd "$TF_DIR"
  terraform init -input=false -upgrade=false >/dev/null
  terraform plan -input=false "${vars[@]}"
}

cmd_infra() {
  need terraform; tokens
  local vars=()
  [ -n "${SADA_ADMIN_CIDRS:-}" ] && vars+=(-var "admin_cidrs=[\"${SADA_ADMIN_CIDRS//,/\",\"}\"]")
  confirm "This creates billable Hetzner resources (server, volume, IPv4) and a firewall."
  cd "$TF_DIR"
  terraform init -input=false -upgrade=false >/dev/null
  terraform apply -input=false ${SADA_YES:+-auto-approve} "${vars[@]}"
  log "server ready; DNS: terraform output dns_a_record"
}

cmd_cluster() {
  need ansible-playbook; need kubectl
  [ -f "$ANS_DIR/inventory.ini" ] || die "no inventory: run ./deploy/up.sh infra first"
  cd "$ANS_DIR"
  ansible-playbook playbook.yml
  log "kubeconfig: $KUBECONFIG"
}

cmd_deps() {
  need helm; need kubectl
  helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
  helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null
  helm repo update >/dev/null
  helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true --wait --timeout 10m
  helm upgrade --install cnpg cnpg/cloudnative-pg \
    --namespace cnpg-system --create-namespace --wait --timeout 10m
  log "cert-manager and CloudNativePG installed"
}

cmd_image() {
  need docker
  if [ ! -d "$REPO_ROOT/web/node_modules" ]; then
    need npm; npm --prefix "$REPO_ROOT/web" ci
  fi
  # The Dockerfile copies web/dist: it has to exist before the build.
  npm --prefix "$REPO_ROOT/web" run build
  docker build -t "$SADA_IMAGE_REPO:$SADA_IMAGE_TAG" "$REPO_ROOT"
  docker push "$SADA_IMAGE_REPO:$SADA_IMAGE_TAG"
  log "pushed $SADA_IMAGE_REPO:$SADA_IMAGE_TAG"
}

# The image must exist on the registry before Helm points the cluster at it, otherwise every pod
# goes into ImagePullBackOff and the back office is down (Recreate strategy).
image_published() {
  local repo=${SADA_IMAGE_REPO#ghcr.io/} token
  [ "$repo" = "$SADA_IMAGE_REPO" ] && return 0 # not ghcr: cannot check anonymously
  token=$(curl -fsS "https://ghcr.io/token?scope=repository:$repo:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  curl -fsS -o /dev/null -H "Authorization: Bearer $token" \
    -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.v2+json" \
    "https://ghcr.io/v2/$repo/manifests/$SADA_IMAGE_TAG"
}

cmd_app() {
  need helm; need kubectl
  # Production is reconciled by Flux: a manual upgrade would be overwritten (or fight it).
  if kubectl -n "$SADA_NAMESPACE" get helmrelease "$SADA_RELEASE" >/dev/null 2>&1 &&
    [ "$(kubectl -n "$SADA_NAMESPACE" get helmrelease "$SADA_RELEASE" -o jsonpath='{.spec.suspend}')" != "true" ]; then
    die "Flux manages this release: run 'flux suspend helmrelease $SADA_RELEASE -n $SADA_NAMESPACE' first (disaster recovery only)"
  fi
  image_published || die "$SADA_IMAGE_REPO:$SADA_IMAGE_TAG is not on the registry yet: wait for the CI on main to go green"
  local args=(
    --namespace "$SADA_NAMESPACE" --create-namespace
    --set "image.repository=$SADA_IMAGE_REPO"
    --set "image.tag=$SADA_IMAGE_TAG"
    --set "ingress.host=$SADA_HOST"
  )
  [ -f "$SADA_VALUES" ] && args+=(-f "$SADA_VALUES")
  if [ -n "${SADA_ACME_EMAIL:-}" ]; then
    args+=(--set certManager.createIssuer=true --set "certManager.email=$SADA_ACME_EMAIL")
  fi
  helm upgrade --install --force-conflicts "$SADA_RELEASE" "$CHART" "${args[@]}" --wait --timeout 15m
  log "back office: https://$SADA_HOST"
}

cmd_status() {
  need kubectl; need helm
  helm list -n "$SADA_NAMESPACE"
  kubectl -n "$SADA_NAMESPACE" get pods,svc,ingress,pvc
  kubectl -n "$SADA_NAMESPACE" get certificate 2>/dev/null || true
}

case "${1:-help}" in
  plan) cmd_plan ;;
  infra) cmd_infra ;;
  cluster) cmd_cluster ;;
  deps) cmd_deps ;;
  image) cmd_image ;;
  app) cmd_app ;;
  status) cmd_status ;;
  all) cmd_plan && cmd_infra && cmd_cluster && cmd_deps && cmd_image && cmd_app && cmd_status ;;
  help | -h | --help)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *) die "unknown command: $1 (try ./deploy/up.sh help)" ;;
esac
