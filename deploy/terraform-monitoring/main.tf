# Private monitoring VM (OTel Collector, Prometheus, Tempo, Grafana), reachable over Tailscale
# only. SEPARATE root module with its OWN state: it never manages (or can recreate) production
# resources. The production SSH key is only READ through a data source. Applying creates
# billable resources.
#
# Exposure: the firewall has NO inbound rule (the optional bootstrap SSH rule is empty by
# default and, when used, limited to the operator's CIDRs). No DNS record is created.

locals {
  monitoring_name = "${var.name_prefix}-monitoring"
}

# Read-only lookup of the SSH key created by the production module (never created nor modified here).
data "hcloud_ssh_key" "deploy" {
  name = var.ssh_key_name
}

resource "hcloud_firewall" "monitoring" {
  name   = "${local.monitoring_name}-fw"
  labels = var.labels

  # Inbound: nothing from the internet. Tailscale connects outward (UDP 41641 / DERP over 443),
  # so replies are handled by connection tracking. The only possible rule is the temporary SSH
  # used by the first Ansible run, before the VM has joined the tailnet.
  dynamic "rule" {
    for_each = length(var.bootstrap_ssh_cidrs) > 0 ? [1] : []
    content {
      direction   = "in"
      protocol    = "tcp"
      port        = "22"
      source_ips  = var.bootstrap_ssh_cidrs
      description = "SSH (bootstrap only, remove after the VM joined the tailnet)"
    }
  }

  # Outbound: only what apt, Docker image pulls, NTP, DNS and Tailscale need.
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "80"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "HTTP (apt mirrors)"
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "443"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "HTTPS (image pulls, Tailscale control plane and DERP)"
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "53"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "DNS over TCP"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "53"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "DNS"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "123"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "NTP"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "3478"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Tailscale STUN"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "41641"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Tailscale direct connections"
  }

  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "ICMP (path MTU)"
  }
}

resource "hcloud_server" "monitoring" {
  name         = local.monitoring_name
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [data.hcloud_ssh_key.deploy.id]
  firewall_ids = [hcloud_firewall.monitoring.id]
  labels       = merge(var.labels, { role = "monitoring" })

  # A primary IPv4 costs 0.50 EUR/month. Docker Hub (Grafana, Prometheus, Tempo images) has no
  # IPv6, so keep it unless the images are mirrored somewhere IPv6-reachable.
  public_net {
    ipv4_enabled = var.ipv4_enabled
    ipv6_enabled = true
  }

  lifecycle {
    ignore_changes = [image]
  }
}

# Hand-off to Ansible (git-ignored, like the production inventory).
resource "local_file" "ansible_inventory_monitoring" {
  filename        = "${path.module}/../ansible/inventory-monitoring.ini"
  file_permission = "0600"
  content = templatefile("${path.module}/inventory.ini.tftpl", {
    server_name  = local.monitoring_name
    server_addr  = var.ipv4_enabled ? hcloud_server.monitoring.ipv4_address : hcloud_server.monitoring.ipv6_address
    ssh_key_path = pathexpand(var.ssh_private_key_path)
  })
}
