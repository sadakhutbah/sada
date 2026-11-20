locals {
  ssh_key_name   = var.ssh_key_name != "" ? var.ssh_key_name : "${var.name_prefix}-deploy"
  ssh_public_key = file(pathexpand(var.ssh_public_key_path))

  # Hetzner volumes show up as /dev/disk/by-id/scsi-0HC_Volume_<id>: a stable path across reboots,
  # /dev/sdb is not. The Ansible playbook formats and mounts this device.
  volume_device = "/dev/disk/by-id/scsi-0HC_Volume_${hcloud_volume.data.id}"

  # Where the kubeconfig is written locally by the Ansible playbook.
  kubeconfig_path = "~/.config/sada-deploy/kubeconfig"

  # Short name of the console record inside the Cloudflare zone ("console").
  console_record_name = trimsuffix(var.console_host, ".${var.cloudflare_zone}")

  # Short name of the ingest record ("ingest").
  ingest_record_name = trimsuffix(var.ingest_host, ".${var.cloudflare_zone}")

  # Who may reach 80/443: Cloudflare only when asked (and then the console must be proxied).
  http_source_ips = var.restrict_http_to_cloudflare ? concat(
    data.cloudflare_ip_ranges.current[0].ipv4_cidrs,
    data.cloudflare_ip_ranges.current[0].ipv6_cidrs,
  ) : var.http_cidrs

  # Where the Ansible inventory is generated (git-ignored).
  inventory_path = "${path.module}/../ansible/inventory.ini"
}

data "hcloud_image" "ubuntu" {
  name = var.image
}

resource "hcloud_ssh_key" "deploy" {
  name       = local.ssh_key_name
  public_key = local.ssh_public_key
  labels     = var.labels
}

resource "hcloud_firewall" "sada" {
  name   = "${var.name_prefix}-fw"
  labels = var.labels

  lifecycle {
    precondition {
      condition     = !var.restrict_http_to_cloudflare || var.console_proxied
      error_message = "restrict_http_to_cloudflare requires console_proxied = true: otherwise the console (and ACME HTTP-01) would be unreachable."
    }
  }

  # Administration: SSH and the k3s API, only from the operator's addresses.
  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.admin_cidrs
    description = "SSH (admin)"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "6443"
    source_ips  = var.admin_cidrs
    description = "k3s API (admin)"
  }

  # Public web: the back office through Traefik, and ACME HTTP-01 challenges. With
  # restrict_http_to_cloudflare only the Cloudflare ranges reach 80/443 (console proxied).
  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "80"
    source_ips  = local.http_source_ips
    description = "HTTP (Traefik, ACME)"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "443"
    source_ips  = local.http_source_ips
    description = "HTTPS (Traefik)"
  }

  # Stream reception: RTMP and SRT pushes from the mosques' computers (OBS).
  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "1935"
    source_ips  = var.media_cidrs
    description = "RTMP ingest"
  }

  rule {
    direction   = "in"
    protocol    = "udp"
    port        = "8890"
    source_ips  = var.media_cidrs
    description = "SRT ingest"
  }

  # WebRTC media of the control room monitors (ICE); the signalling goes through the back office.
  rule {
    direction   = "in"
    protocol    = "udp"
    port        = "8189"
    source_ips  = var.http_cidrs
    description = "WebRTC (control room monitors)"
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "1-65535"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Outgoing TCP (updates, ACME, packages, image pulls, RTMP destinations)"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "1-65535"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Outgoing UDP (DNS, SRT, NTP)"
  }

  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Outgoing ICMP (path MTU, diagnostics)"
  }
}

resource "hcloud_server" "sada" {
  name         = "${var.name_prefix}-app"
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.deploy.id]
  firewall_ids = [hcloud_firewall.sada.id]
  labels       = var.labels

  public_net {
    ipv4_enabled = true
    ipv6_enabled = false
  }

  # The image name resolves to the latest build: do not rebuild the server (and lose the
  # volumes' mount point) because Ubuntu published a new one. Recreate on purpose instead.
  lifecycle {
    ignore_changes = [image]
  }
}

resource "hcloud_volume" "data" {
  name              = "${var.name_prefix}-data"
  size              = var.data_volume_size
  server_id         = hcloud_server.sada.id
  labels            = var.labels
  delete_protection = var.data_volume_delete_protection
  # Formatting is Ansible's job (ext4, only when the volume is empty): Terraform would format
  # once and never look again. Attachment stays managed here so a rebuilt server gets it back.
}

# Inventory read by `ansible-playbook -i inventory.ini playbook.yml`: regenerated at every apply,
# never committed (the address of the server does not belong to the repository).
resource "local_file" "ansible_inventory" {
  filename        = local.inventory_path
  file_permission = "0600"
  content = templatefile("${path.module}/inventory.ini.tftpl", {
    server_name     = "${var.name_prefix}-app"
    server_ipv4     = hcloud_server.sada.ipv4_address
    ssh_key_path    = trimsuffix(pathexpand(var.ssh_public_key_path), ".pub")
    volume_device   = local.volume_device
    data_mount_path = "/mnt/sada-data"
    k3s_mount_path  = "/mnt/sada-data/k3s"
    kubeconfig_path = local.kubeconfig_path
  })
}
