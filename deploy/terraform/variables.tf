variable "hcloud_token" {
  description = "Hetzner Cloud API token (read/write). Prefer TF_VAR_hcloud_token over a tfvars file."
  type        = string
  sensitive   = true
}

variable "name_prefix" {
  description = "Prefix of every resource created (server, volume, firewall, SSH key)."
  type        = string
  default     = "sada"
}

variable "location" {
  description = "Hetzner location: nbg1 (Nuremberg) or fsn1 (Falkenstein). The volume follows."
  type        = string
  default     = "fsn1"

  validation {
    condition     = contains(["nbg1", "fsn1"], var.location)
    error_message = "location must be nbg1 or fsn1 (the volume has to be in the same location as the server)."
  }
}

variable "server_type" {
  description = "Server type. cx33 = 4 vCPU (shared), 8 GB RAM, 80 GB disk, ~10 EUR/month."
  type        = string
  default     = "cx33"
}

variable "image" {
  description = "Base image (Ubuntu 24.04 LTS; the Ansible playbook targets it)."
  type        = string
  default     = "ubuntu-24.04"
}

variable "ssh_public_key_path" {
  description = "Public key injected into the server (root login)."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_key_name" {
  description = "Name of the SSH key in the Hetzner project (default: <name_prefix>-deploy)."
  type        = string
  default     = ""
}

variable "console_host" {
  description = "Public host name of the back-office console (Traefik Ingress + Let's Encrypt certificate)."
  type        = string
  default     = "console.sadakhutbah.org"
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with DNS edit on the zone (IP-filtered). Prefer TF_VAR_cloudflare_api_token."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone" {
  description = "Cloudflare zone holding the console record."
  type        = string
  default     = "sadakhutbah.org"
}

variable "admin_cidrs" {
  description = "CIDRs allowed on 22/tcp (SSH) and 6443/tcp (k3s API). Set your own addresses, not 0.0.0.0/0."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0
    error_message = "admin_cidrs must list at least one CIDR, otherwise SSH and the k3s API are locked."
  }
}

variable "http_cidrs" {
  description = "CIDRs allowed on 80/tcp and 443/tcp (Traefik: back office, ACME HTTP-01)."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "media_cidrs" {
  description = "CIDRs allowed on 1935/tcp (RTMP) and 8890/udp (SRT): the mosques that push their stream."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "data_volume_size" {
  description = "Size in GB of the data volume (recordings, state, PostgreSQL). Minimum 10."
  type        = number
  default     = 50

  validation {
    condition     = var.data_volume_size >= 10
    error_message = "Hetzner volumes start at 10 GB."
  }
}

variable "data_volume_delete_protection" {
  description = "Protect the data volume against deletion (terraform destroy would then fail until lifted)."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels put on every Hetzner resource."
  type        = map(string)
  default = {
    app       = "sada"
    managedby = "terraform"
  }
}
