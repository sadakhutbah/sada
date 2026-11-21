# Deploying Sadā on one Hetzner server

Small budget, one machine: a Hetzner **cx33** (4 vCPU, 8 GB RAM, 80 GB disk) in `fsn1` or
`nbg1`, running **k3s** (Traefik + ServiceLB included). One Hetzner **volume** holds every
piece of data (recordings, orchestrator state, PostgreSQL) and is mounted by Ansible.

```
                      1935/tcp (RTMP)  8890/udp (SRT)
mosques' OBS  ────────────────────────────────►  receiver (MediaMTX)  ──rtsp:8554──►  orchestrator-<slug>
                                                        │ authorisation                    │
                                                        ▼                                  │ SADA_AGENT_TOKEN
                                                   backoffice  ◄───────────────────────────┘
                                                        │
                                    Traefik / Ingress : console.sadakhutbah.org (Let's Encrypt)
                                                        │
                                                   PostgreSQL (CloudNativePG, 1 instance)
```

| Piece | Where |
|---|---|
| `deploy/terraform/` | server, firewall, SSH key, data volume, Ansible inventory |
| `deploy/terraform-monitoring/` | optional private monitoring VM, SEPARATE Terraform root with its own state (never touches production), see `ansible/roles/monitoring/README.md` |
| `deploy/ansible/` | volume formatted/mounted, k3s installed, kubeconfig fetched locally |
| `deploy/helm/sada/` | back office, MediaMTX receiver, one orchestrator per mosque, PostgreSQL |
| `deploy/up.sh` | runs the four steps above in order |

**Cost**: about €12/month — cx33 ~€9, 50 GB volume ~€2.2, primary IPv4 €0.5. Nothing else is
created (no load balancer, no managed database, no backup).

## Prerequisites

- `terraform`, `ansible-playbook` (+ `ansible-core`), `helm` (3.x), `kubectl`, `docker`, `npm`
- an SSH key pair (`~/.ssh/id_ed25519.pub` by default)
- a Hetzner Cloud API token with read/write, kept out of the repository:
  `~/.config/sada-deploy/hcloud_token`, or `export TF_VAR_hcloud_token=...`
- a Cloudflare API token with DNS edit on `sadakhutbah.org` (IP-filtered: allow the address you
  deploy from), kept in `~/.config/sada-deploy/cloudflare_token`, or
  `export TF_VAR_cloudflare_api_token=...`
- the image pushed to a registry (`ghcr.io/<owner>/sada`) — see *Image* below

## 1. Infrastructure (Terraform)

```sh
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars     # admin_cidrs, location, volume size
export TF_VAR_hcloud_token=$(cat ~/.config/sada-deploy/hcloud_token)
export TF_VAR_cloudflare_api_token=$(cat ~/.config/sada-deploy/cloudflare_token)
terraform init
terraform plan                                   # 6 resources, nothing created yet
terraform apply                                  # server, firewall, key, volume, DNS record
terraform output dns_a_record                    # console.sadakhutbah.org A <ip>, TTL 300, not proxied
```

If an A record for `console.sadakhutbah.org` already exists, delete it (or
`terraform import cloudflare_dns_record.console <zone_id>/<record_id>`) before applying, otherwise
you end up with two records answering for the same name.

The console record (`console.sadakhutbah.org`, zone `sadakhutbah.org`) is created in Cloudflare
by Terraform, **not proxied by default** (`console_proxied = false`): Let's Encrypt validates over HTTP-01 on the server itself, and the
mosques push straight to it. The stream ingest has its own name, `ingest.sadakhutbah.org` (RTMP 1935/tcp and SRT 8890/udp, never proxied). Putting the console behind the Cloudflare proxy is a staged operation: see [`cloudflare/PLAN.md`](cloudflare/PLAN.md).

`admin_cidrs` is **required**: SSH (22) and the k3s API (6443) are closed to everything else.
80/443 (Traefik, ACME) and 1935/tcp + 8890/udp (stream ingest) stay public; narrow them with
`http_cidrs` / `media_cidrs` if you know the mosques' addresses.

State is local (`terraform.tfstate`, git-ignored) and the volume is delete-protected by default.

## 2. Cluster (Ansible)

`terraform apply` wrote `deploy/ansible/inventory.ini`. Then:

```sh
cd deploy/ansible
ansible-playbook playbook.yml
```

It waits for cloud-init, formats the volume as ext4 if it carries no filesystem, mounts it at
`/mnt/sada-data` (`/etc/fstab`, by UUID), bind-mounts `/mnt/sada-data/k3s` onto
`/var/lib/rancher/k3s/storage` (where local-path keeps every PV; k3s then requires both mounts),
installs k3s, and writes the kubeconfig to
`~/.config/sada-deploy/kubeconfig` with the server's public IPv4 (in-cluster it would say
`127.0.0.1`). Export it once:

```sh
export KUBECONFIG=~/.config/sada-deploy/kubeconfig
kubectl get nodes
```

## 3. Cluster add-ons (Helm)

```sh
helm repo add jetstack https://charts.jetstack.io && helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true --wait
helm upgrade --install cnpg cnpg/cloudnative-pg -n cnpg-system --create-namespace --wait
```

## 4. Image

The Dockerfile copies `web/dist`, which is built from `web/` (`npm --prefix web run build`,
i.e. `make front`). Build and push:

```sh
make front                                  # needs `npm ci` in web/ the first time
docker build -t ghcr.io/<owner>/sada:<tag> .
docker push ghcr.io/<owner>/sada:<tag>
```

One image, two roles: `python -m sada.backoffice serve` (port 8090) and
`python -m sada.orchestrator` (port 8088, one container per mosque). `image.repository` and
`image.tag` are chart values; the default repository is `ghcr.io/thesadaproject/sada`, so change
it unless that is your registry.

## 5. Sadā

**Production is driven by Flux CD**, not by this command. The cluster state (HelmRelease
`sada`, production values, image automation) lives in the private repository
`thesadaproject/sada-gitops` (`clusters/prod/`). Flux reads the chart from this repository
(`deploy/helm/sada`, branch `main`) and the image from the CI: every push to `main` builds
`ghcr.io/thesadaproject/sada:main-<run number>-<short sha>`, Flux picks the highest run number,
commits the new `image.tag` to `sada-gitops` and upgrades the release (a few minutes after
the CI turns green). Prod never runs an image built on a workstation.

- Follow a rollout: `flux get helmreleases -n sada`, `flux get images all -A`,
  `helm -n sada history sada`.
- Roll back: revert the automation commit in `sada-gitops` (or pin `image.tag` there and
  suspend the automation: `flux suspend image update sada`). In an emergency,
  `flux suspend helmrelease sada -n sada` then `helm -n sada rollback sada <revision>`.
- Pause deployments: `flux suspend helmrelease sada -n sada`; resume with
  `flux resume helmrelease sada -n sada`.
- Change a production value (mosques, host, fps…): edit the HelmRelease in `sada-gitops`.
  Values set by hand with `helm` or `kubectl` are overwritten at the next reconciliation.
- Chart changes on `main` are applied when the chart `version` in `Chart.yaml` is bumped.
- Never delete the HelmRelease: uninstalling the release deletes the data PVC and the
  PostgreSQL cluster.

The manual command below is for a fresh cluster or disaster recovery only, with Flux
suspended:

```sh
helm upgrade --install sada deploy/helm/sada -n sada --create-namespace \
  --set image.repository=ghcr.io/<owner>/sada --set image.tag=<tag> \
  --set certManager.createIssuer=true --set certManager.email=you@example.org \
  -f deploy/values.local.yaml
```

`deploy/values.local.yaml` (git-ignored) lists the mosques:

```yaml
mosques:
  - slug: masjid-al-nour
    tokenSecret: sada-masjid-al-nour
```

Defaults: Ingress host `console.sadakhutbah.org`, TLS through cert-manager with the
`letsencrypt-prod` ClusterIssuer (HTTP-01 solved by Traefik), PostgreSQL managed by
CloudNativePG (`SADA_DATABASE_URL` read from the `<cluster>-app` Secret, key `uri`, which is a
`postgresql://` URL — the application accepts it as is). `database.mode: sqlite` skips
PostgreSQL entirely.

First run, from the cluster:

```sh
kubectl -n sada exec -it deploy/backoffice -- python -m sada.backoffice create-admin \
  --username admin --mosque masjid-al-nour --mosque-name "Masjid al-Nour"
kubectl -n sada exec -it deploy/backoffice -- python -m sada.backoffice mosque \
  --slug masjid-al-nour --agent-token            # put the token in the mosque's Secret
kubectl -n sada create secret generic sada-masjid-al-nour --from-literal=token=<token>
kubectl -n sada exec -it deploy/backoffice -- python -m sada.backoffice mosque \
  --slug masjid-al-nour --publish-key --orchestrator-url http://orchestrator-masjid-al-nour:8088
kubectl -n sada rollout restart deploy/orchestrator-masjid-al-nour
```

The stream key printed by `--publish-key` goes into OBS:

```
RTMP  rtmp://<server-ip>:1935/live/<slug>?user=<slug>&pass=<stream key>
SRT   srt://<server-ip>:8890?streamid=publish:live/<slug>:<slug>:<stream key>
```

`./deploy/up.sh` chains every step (`plan`, `infra`, `cluster`, `deps`, `image`, `app`,
`status`, `all`); settings come from the environment or `deploy/deploy.env`.

## Checking it without creating anything

```sh
terraform -chdir=deploy/terraform fmt -check
terraform -chdir=deploy/terraform init && terraform -chdir=deploy/terraform validate
terraform -chdir=deploy/terraform plan -var 'admin_cidrs=["203.0.113.10/32"]'   # 6 to add
ansible-playbook -i deploy/ansible/inventory.example.ini --syntax-check deploy/ansible/playbook.yml
helm lint deploy/helm/sada --strict
helm template sada deploy/helm/sada -n sada -f deploy/helm/sada/ci/test-values.yaml
```

`terraform plan` reads the token (read-only API calls: image lookup) but creates nothing.

## Telemetry (optional)

The back office (`sada-backoffice`) and each orchestrator (`sada-orchestrator`, resource attribute
`sada.mosque`) can export **metrics and traces** over OTLP/HTTP to a collector (OpenTelemetry Collector →
Prometheus / Tempo / Grafana, ideally on a private network such as Tailscale). It is **off by default**:
no exporter, no connection, no change of behaviour until one of these is set (add them to
`backoffice.extraEnv` and `orchestrator.extraEnv`):

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | switches the export on (also `..._METRICS_ENDPOINT` / `..._TRACES_ENDPOINT`) |
| `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_METRIC_EXPORT_INTERVAL` | standard SDK settings |
| `SADA_TELEMETRY=1` / `0` | force on (default local endpoint) / off, whatever the endpoint |
| `OTEL_SDK_DISABLED=true` | force off |

Metrics: `sada.orchestrator.state{state}`, `sada.state.transitions{from_state,to_state}`, `sada.stream.on`,
`sada.stream.live.duration`, `sada.encoder.up`, `sada.encoder.restarts`, `sada.ingest.connected`,
`sada.job.runs` / `sada.job.duration{kind,status}`, `sada.llm.calls` / `.duration{protocol,status}`,
`sada.transcription.calls` / `.duration{engine,stage,status}`, `sada.notification.sent` /
`.duration{channel,status}`, plus the HTTP server metrics of the back office. Traces: back-office requests
(route template only), jobs, LLM and transcription calls.

The resource carries only `service.name` and `sada.mosque`: `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SERVICE_NAME`
are deliberately ignored, exemplars are disabled, and an incoming `traceparent` / `tracestate` is never
trusted. A warning is logged (host only) if the endpoint is not private (loopback, RFC 1918, 100.64/10,
`*.ts.net`) or not https.

Nothing sensitive is exported: only an allowlist of low-cardinality labels (`sada/telemetry.py`) leaves
the process. No prompt, transcript, LLM answer, name, e-mail, IP address, header, request body, URL path
or query string, stream key or secret; no exception message; no logs. `/health` is not traced.

## Limits, by design

- **One node**: the PVC is `ReadWriteOnce` and shared by the back office and the orchestrators.
  Lose the node and the service stops; the volume survives (`delete_protection`).
- **No backup**: CloudNativePG keeps its data in the volume with no schedule. Add one (Barman to
  object storage) before it matters.
- **No HA**: one back office (`Recreate`), one PostgreSQL instance, one MediaMTX.
- **One orchestrator per mosque** uses ffmpeg and renders screens: a cx33 holds a handful
  before the memory limit of `orchestrator.resources` bites (3 GiB each in the defaults).
- **DNS**: the record lives in Cloudflare and is not proxied, so the server's IPv4 is public
  and Let's Encrypt can reach it (HTTP-01).
