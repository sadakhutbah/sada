output "monitoring_server" {
  description = "Monitoring VM. Reach Grafana over Tailscale, never on these addresses."
  value = {
    name = hcloud_server.monitoring.name
    type = "${hcloud_server.monitoring.server_type} in ${hcloud_server.monitoring.location}"
    ipv4 = hcloud_server.monitoring.ipv4_address
    ipv6 = hcloud_server.monitoring.ipv6_address
  }
}
