# monitoring role

Private VM: Tailscale + Docker, then OTel Collector, Prometheus (30 d / 10 GB), Tempo (14 d, local
storage) and Grafana, all images pinned (`defaults/main.yml`). Nothing is public: the Hetzner
firewall has no inbound rule, and every published port is bound to the Tailscale address.

## Apply (needs the owner's go: billable, ~0.0096 EUR/h with IPv4)
The VM has its OWN Terraform root module, `deploy/terraform-monitoring/`, with its own local state
(git-ignored). It is separate from `deploy/terraform/` (production, whose local state lives on the
operator's machine): applying the monitoring module can never create, change or destroy a
production resource. It only READS the production SSH key (`data "hcloud_ssh_key"`, default name
`sada-deploy`). Never run it with `-state` pointing at the production state.

1. Tailscale admin console: add the ACL below, then create a **single-use, pre-approved, tagged**
   (`tag:sada-monitoring`) auth key, non-ephemeral (the VM is persistent), short expiry. Save it as
   `~/.config/sada-deploy/tailscale_authkey` (0600) or export `SADA_TS_AUTHKEY`.
2. ```
   cd deploy/terraform-monitoring
   export TF_VAR_hcloud_token="$(cat ~/.config/sada-deploy/hcloud_token)"
   terraform init && terraform plan -var 'bootstrap_ssh_cidrs=["<your ip>/32"]'   # 3 to add, nothing else
   terraform apply -var 'bootstrap_ssh_cidrs=["<your ip>/32"]'
   ```
   `deploy/up.sh` does not touch this module.
3. `cd deploy/ansible && ansible-playbook -i inventory-monitoring.ini monitoring.yml`
4. Close public SSH: `terraform apply -var 'bootstrap_ssh_cidrs=[]'` (from `deploy/terraform-monitoring`).
   From then on SSH is reachable over the tailnet only: `-e ansible_host=<tailscale ip>`.
5. Open `http://<tailscale ip>:3000` (user `admin`, password in
   `~/.config/sada-deploy/grafana_admin_password`).

Rollback: `terraform destroy` in `deploy/terraform-monitoring` removes the VM and its firewall (not
the SSH key, which belongs to production); remove the machine from the Tailscale admin console and
revoke the auth key.

## Tailscale ACL (policy file)
```jsonc
{
  "tagOwners": {
    "tag:sada-monitoring": ["autogroup:admin"],
    "tag:sada-prod":       ["autogroup:admin"]      // the k3s node, once it joins the tailnet
  },
  "acls": [
    // owner devices -> Grafana, and SSH once the public rule is closed
    {"action": "accept", "src": ["autogroup:member"], "dst": ["tag:sada-monitoring:22,3000"]},
    // production -> OTLP ingestion only (gRPC 4317, HTTP 4318)
    {"action": "accept", "src": ["tag:sada-prod"], "dst": ["tag:sada-monitoring:4317,4318"]}
    // tag:sada-monitoring has no rule as source: it initiates nothing on the tailnet (Prometheus
    // kubelet scraping, if enabled, needs one more rule: monitoring -> tag:sada-prod:10250)
  ]
}
```
Note: with `autogroup:member` replace by the owner's user/devices if the tailnet has other members.

## Hardening in this role
- Container egress: persisted `DOCKER-USER` rules (`/usr/local/sbin/sada-docker-egress`, run as
  `ExecStartPost` of docker) drop forwarded traffic from Docker bridges except to other bridges and
  `100.64.0.0/10`. The Collector and Grafana therefore cannot phone home or exfiltrate over the
  internet (Docker pulls run from the host and are unaffected). IPv6 is not enabled in Docker.
- Collector: `transform/allowlist` deletes every attribute key not in the allowlists
  (`monitoring_otel_resource_keys`, `monitoring_otel_signal_keys`), blanks span status messages, and
  exemplars are not exposed (no OpenMetrics, Prometheus without exemplar storage).
- Kubelet scrape: HTTPS with verification. Disabled unless `monitoring_kubelet_targets` is set,
  which then requires `monitoring_kubelet_ca_file` (no `insecure_skip_verify`).
- Tailscale key passed by file (`--auth-key=file:`), not argv, and removed after use.
- Grafana: no anonymous access, public dashboards disabled. Containers: read-only/cap_drop/
  no-new-privileges where possible, memory/CPU/pids limits (`monitoring_limits`).
- sshd: key-only (`00-sada-hardening.conf`), unattended security upgrades.
- The playbook asserts that every stack port listens on the Tailscale address only.

## Sending data from the k3s pods
The k3s node does not run Tailscale today (`playbook.yml` installs none). Minimal path: install
Tailscale on the node (host network, so pods route to `100.64.0.0/10` through NAT on the node), then
in the Helm values (`sada-gitops`), for `backoffice.extraEnv` and `orchestrator.extraEnv`:

```yaml
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://sada-monitoring:4318"}   # or the 100.x address
- {name: OTEL_EXPORTER_OTLP_PROTOCOL, value: "http/protobuf"}
- {name: OTEL_SERVICE_NAME, value: "sada-orchestrator"}
- {name: OTEL_RESOURCE_ATTRIBUTES, value: "k8s.namespace.name=sada"}
```
Use the 100.x address (pod DNS does not resolve MagicDNS names). The app is not instrumented yet:
the dashboard queries are placeholders for the expected metric names. The kubelet/cAdvisor CPU and
memory panels need the node on the tailnet and `monitoring_kubelet_targets` + a token.

## Residual points
- The Hetzner firewall still allows outbound 80/443 for the host (apt, image pulls, Tailscale);
  containers are blocked by the DOCKER-USER rules, phone-home is also disabled in configuration.
- Docker Hub has no IPv6, hence the primary IPv4 (0.50 EUR/month) kept by default.
