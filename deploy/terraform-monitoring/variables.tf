variable "hcloud_token" {
  description = "Hetzner Cloud API token (read/write). Use TF_VAR_hcloud_token, never a tfvars file."
  type        = string
  sensitive   = true
}

variable "name_prefix" {
  description = "Prefix of the monitoring resources (server <prefix>-monitoring, firewall <prefix>-monitoring-fw)."
  type        = string
  default     = "sada"
}

variable "location" {
  description = "Hetzner location. cx23 is available in fsn1, not in nbg1."
  type        = string
  default     = "fsn1"
}

variable "image" {
  description = "Base image (Ubuntu 24.04 LTS; the Ansible role targets it)."
  type        = string
  default     = "ubuntu-24.04"
}

variable "server_type" {
  description = "Server type. cx23 = 2 vCPU (shared), 4 GB RAM, 40 GB disk, 0.0088 EUR/h, 5.49 EUR/month excl. VAT and IPv4."
  type        = string
  default     = "cx23"
}

variable "ipv4_enabled" {
  description = "Give the VM a primary IPv4 (0.50 EUR/month). Needed to pull images from Docker Hub, which has no IPv6."
  type        = bool
  default     = true
}

variable "ssh_key_name" {
  description = "Name of the EXISTING SSH key in the Hetzner project (created by the production module; read-only lookup)."
  type        = string
  default     = "sada-deploy"
}

variable "ssh_private_key_path" {
  description = "Private key matching ssh_key_name, written into the generated Ansible inventory."
  type        = string
  default     = "~/.config/sada-deploy/id_ed25519"
}

variable "bootstrap_ssh_cidrs" {
  description = "CIDRs allowed on 22/tcp, ONLY for the first Ansible run (before the VM joins the tailnet). Apply again with [] afterwards: SSH is then reachable over the tailnet only."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels put on every Hetzner resource."
  type        = map(string)
  default = {
    app       = "sada"
    managedby = "terraform"
    role      = "monitoring"
  }
}
