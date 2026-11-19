output "server_ipv4" {
  description = "Public IPv4 of the server. Put it in the DNS A record of the back-office host (see dns_a_record)."
  value       = hcloud_server.sada.ipv4_address
}

output "dns_a_record" {
  description = "A record created in Cloudflare for the back-office console (not proxied, TTL 300)."
  value = {
    name    = var.console_host
    zone    = var.cloudflare_zone
    type    = "A"
    content = hcloud_server.sada.ipv4_address
    ttl     = 300
    proxied = false
  }
}

output "console_url" {
  description = "Once DNS propagated and the chart installed: the back office console."
  value       = "https://${var.console_host}"
}

output "server_name" {
  description = "Server name (also the Ansible host name)."
  value       = hcloud_server.sada.name
}

output "server_type" {
  description = "Server type and location actually used."
  value       = "${hcloud_server.sada.server_type} in ${hcloud_server.sada.location}"
}

output "volume_id" {
  description = "Hetzner volume id holding recordings, state and PostgreSQL."
  value       = hcloud_volume.data.id
}

output "volume_linux_device" {
  description = "Device path of the data volume inside the server (formatted and mounted by Ansible)."
  value       = local.volume_device
}

output "ssh_command" {
  description = "SSH into the server."
  value       = "ssh root@${hcloud_server.sada.ipv4_address}"
}

output "ansible_inventory" {
  description = "Inventory written by Terraform for the Ansible playbook."
  value       = abspath(local.inventory_path)
}

output "kubeconfig_path" {
  description = "Kubeconfig written locally by the Ansible playbook."
  value       = local.kubeconfig_path
}

output "monitoring_server" {
  description = "Monitoring VM (null when enable_monitoring = false). Reach Grafana over Tailscale, never on this address."
  value = var.enable_monitoring ? {
    name = hcloud_server.monitoring[0].name
    type = "${hcloud_server.monitoring[0].server_type} in ${hcloud_server.monitoring[0].location}"
    ipv4 = hcloud_server.monitoring[0].ipv4_address
    ipv6 = hcloud_server.monitoring[0].ipv6_address
  } : null
}
