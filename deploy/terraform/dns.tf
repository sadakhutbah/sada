# DNS in the Cloudflare zone.
#
# - console (back office): an A record. Not proxied by default (certificate issued over HTTP-01 on
#   this very server). With var.console_proxied = true it goes through the Cloudflare proxy; that
#   requires DNS-01 (or an origin certificate) for the certificate, see deploy/cloudflare/PLAN.md.
# - ingest (RTMP 1935/tcp + SRT 8890/udp): ONE name for both protocols, never proxied (the
#   Cloudflare proxy carries HTTP(S) only, not RTMP/SRT).
#
# The token is IP-filtered: it must be passed as TF_VAR_cloudflare_api_token (see
# ~/.config/sada-deploy/cloudflare_token), never committed.
# Nothing here is applied by CI: `terraform apply` is run by the operator, after reading the plan.

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
  ttl     = var.console_proxied ? 1 : 300 # 1 = automatic, the only TTL of a proxied record
  proxied = var.console_proxied
  comment = "Sadā back office (managed by terraform, deploy/terraform)"

  lifecycle {
    precondition {
      condition     = endswith(var.console_host, ".${var.cloudflare_zone}")
      error_message = "console_host (${var.console_host}) must be a subdomain of cloudflare_zone (${var.cloudflare_zone})."
    }
  }
}

# Stream ingest, one name for RTMP and SRT: rtmp://<ingest_host>:1935/live/<slug> and
# srt://<ingest_host>:8890?streamid=... Grey cloud on purpose: proxied is NOT a variable.
resource "cloudflare_dns_record" "ingest" {
  count = var.create_ingest_record ? 1 : 0

  zone_id = data.cloudflare_zone.console.zone_id
  name    = local.ingest_record_name
  type    = "A"
  content = hcloud_server.sada.ipv4_address
  ttl     = 300
  proxied = false
  comment = "Sadā stream ingest RTMP+SRT, never proxied (managed by terraform, deploy/terraform)"

  lifecycle {
    precondition {
      condition     = endswith(var.ingest_host, ".${var.cloudflare_zone}")
      error_message = "ingest_host (${var.ingest_host}) must be a subdomain of cloudflare_zone (${var.cloudflare_zone})."
    }
    precondition {
      condition     = var.ingest_host != var.console_host
      error_message = "ingest_host must differ from console_host: the console may be proxied, the ingest never is."
    }
  }
}

# Encryption mode of the whole zone. "strict" (Full strict) makes Cloudflare validate the origin
# certificate (Let's Encrypt or Cloudflare Origin CA). Off by default: it applies to every
# hostname of the zone, landing included.
resource "cloudflare_zone_setting" "ssl" {
  count = var.manage_zone_ssl_strict ? 1 : 0

  zone_id    = data.cloudflare_zone.console.zone_id
  setting_id = "ssl"
  value      = "strict"
}

# Published Cloudflare ranges, used to restrict 80/443 on the Hetzner firewall (read-only).
data "cloudflare_ip_ranges" "current" {
  count = var.restrict_http_to_cloudflare ? 1 : 0
}
