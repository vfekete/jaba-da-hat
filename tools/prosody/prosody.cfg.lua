-- Throw-away Prosody for jaba-da-hat development (tools/test-server.sh). Not for production.
admins = { }
modules_enabled = {
    "roster"; "saslauth"; "tls"; "disco"; "carbons"; "pep"; "private"; "blocklist";
    "vcard_legacy"; "version"; "uptime"; "time"; "ping"; "register"; "mam"; "smacks";
    "csi_simple"; "offline";
}
allow_registration = false
c2s_require_encryption = true
s2s_require_encryption = true
authentication = "internal_hashed"
storage = "internal"
archive_expires_after = "1w"
default_archive_policy = true
log = { info = "*console" }
pidfile = "/var/run/prosody/prosody.pid"
certificates = "certs"

-- HTTP Upload (XEP-0363): files are served over HTTPS; the container's 5281 is mapped to 15281.
http_ports = { }
https_ports = { 5281 }
https_ssl = { certificate = "/etc/prosody/certs/localhost.crt"; key = "/etc/prosody/certs/localhost.key"; }
http_external_url = "https://localhost:15281/"

VirtualHost "localhost"
    ssl = { certificate = "/etc/prosody/certs/localhost.crt"; key = "/etc/prosody/certs/localhost.key"; }

Component "upload.localhost" "http_file_share"
    http_host = "localhost"
    http_file_share_size_limit = 10 * 1024 * 1024
    http_file_share_expires_after = 60 * 60
