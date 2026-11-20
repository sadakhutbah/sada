output "server_ipv4" {
  description = "Public IPv4 of the server. Put it in the DNS A record of the back-office host (see dns_a_record)."
  value       = hcloud_server.sada.ipv4_address
}

output "dns_a_record" {
  description = "A record created in Cloudflare for the back-office console (proxied only if console_proxied)."
  value = {
    name    = var.console_host
    zone    = var.cloudflare_zone
    type    = "A"
    content = hcloud_server.sada.ipv4_address
    ttl     = var.console_proxied ? 1 : 300
    proxied = var.console_proxied
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

output "dns_ingest_record" {
  description = "Single ingest name for RTMP (1935/tcp) and SRT (8890/udp): never proxied. Empty when create_ingest_record = false."
  value = var.create_ingest_record ? {
    name    = var.ingest_host
    zone    = var.cloudflare_zone
    type    = "A"
    content = hcloud_server.sada.ipv4_address
    ttl     = 300
    proxied = false
  } : null
}

output "ingest_urls" {
  description = "Templates of the ingest URLs given to the mosques (replace <slug> and <key>)."
  value = {
    rtmp = "rtmp://${var.ingest_host}:1935/live/<slug>?user=<slug>&pass=<key>"
    srt  = "srt://${var.ingest_host}:8890?streamid=publish:live/<slug>:<slug>:<key>"
  }
}
