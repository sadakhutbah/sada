terraform {
  required_version = ">= 1.6.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.54"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # Local state: this is a one-machine deployment, the state stays on the operator's laptop
  # (git-ignored). Move it to an object storage backend before two people deploy.
}

provider "hcloud" {
  token = var.hcloud_token
}

# DNS of the console (console.sadakhutbah.org). The token is IP-filtered: keep it in
# ~/.config/sada-deploy/cloudflare_token and pass TF_VAR_cloudflare_api_token from there.
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
