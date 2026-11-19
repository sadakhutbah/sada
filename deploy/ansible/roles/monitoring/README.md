# monitoring role

Private VM: Tailscale + Docker, then OTel Collector, Prometheus (30 d / 10 GB), Tempo (14 d, local
storage) and Grafana, all images pinned (`defaults/main.yml`). Nothing is public: the Hetzner
firewall has no inbound rule, and every published port is bound to the Tailscale address.

## Apply (needs the owner's go: billable, ~0.0096 EUR/h with IPv4)
1. Create in the Tailscale admin console a one-off, pre-approved, tagged (e.g. `tag:sada-monitoring`)
   auth key; save it as `~/.config/sada-deploy/tailscale_authkey` (0600). ACL: let only the owner's
   devices reach `tag:sada-monitoring` on 3000, 4317, 4318 and the pods' node reach 4317/4318.
2. `terraform apply -var enable_monitoring=true -var 'monitoring_bootstrap_ssh_cidrs=["<your ip>/32"]'`
   (from `deploy/terraform`; tokens as for `up.sh`). Do NOT use `up.sh infra` blindly: it would
   apply the same plan, which is fine, but check `terraform plan` first.
3. `cd deploy/ansible && ansible-playbook -i inventory-monitoring.ini monitoring.yml`
4. Close SSH: apply again with `monitoring_bootstrap_ssh_cidrs=[]`. From then on use
   `-e ansible_host=<tailscale ip>`.
5. Open `http://<tailscale ip>:3000` (user `admin`, password in
   `~/.config/sada-deploy/grafana_admin_password`).

Rollback: `terraform apply -var enable_monitoring=false` destroys the VM and its firewall; remove the
machine from the Tailscale admin console.

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
- The Collector and Grafana containers can reach the internet on 80/443 (Hetzner egress allows it
  for apt/images); phone-home is disabled in their configuration, not by a network block.
- Docker Hub has no IPv6, hence the primary IPv4 (0.50 EUR/month) kept by default.
