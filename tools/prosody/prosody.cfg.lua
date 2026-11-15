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
VirtualHost "localhost"
    ssl = { certificate = "/etc/prosody/certs/localhost.crt"; key = "/etc/prosody/certs/localhost.key"; }
