terraform {
  required_version = ">= 1.6.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.54"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }

  # Local state, SEPARATE from production (deploy/terraform). Git-ignored. Do not copy or merge it
  # with the production state: the two modules share nothing but the Hetzner project.
}

provider "hcloud" {
  token = var.hcloud_token
}
