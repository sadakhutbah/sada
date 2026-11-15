# DNS of the console, in the Cloudflare zone: a plain A record (not proxied) so that the
# certificate is issued by Let's Encrypt over HTTP-01 on this very server, and so that the
# mosques' pushes and the API keep talking to the machine directly.
#
# The token is IP-filtered: it must be passed as TF_VAR_cloudflare_api_token (see
# ~/.config/sada-deploy/cloudflare_token), never committed.

data "cloudflare_zone" "console" {
  filter = {
    name = var.cloudflare_zone
  }
}

resource "cloudflare_dns_record" "console" {
  zone_id = data.cloudflare_zone.console.zone_id
  name    = local.console_record_name
  type    = "A"
  content = hcloud_server.sada.ipv4_address
  ttl     = 300
  proxied = false
  comment = "Sadā back office (managed by terraform, deploy/terraform)"

  lifecycle {
    precondition {
      condition     = endswith(var.console_host, ".${var.cloudflare_zone}")
      error_message = "console_host (${var.console_host}) must be a subdomain of cloudflare_zone (${var.cloudflare_zone})."
    }
  }
}
