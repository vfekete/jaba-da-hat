#!/usr/bin/env bash
# Throw-away Prosody XMPP server in a container, for development and integration tests.
#   tools/test-server.sh start   generate a test CA + certificate, start Prosody, create test users
#   tools/test-server.sh stop    remove the container (all accounts and archives are gone)
# Accounts (test-only, not secrets): me@localhost, alice@localhost, bob@localhost — password "test-password".
# Port 15222 on 127.0.0.1. The CA for --ca-file is build/prosody/ca.crt.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
NAME=jdh-prosody
IMAGE=prosodyim/prosody:13.0
PORT=${JDH_TEST_PORT:-15222}
DIR=build/prosody
USERS=(me alice bob)
PASSWORD=test-password

certs() {
    mkdir -p "$DIR"
    [[ -f $DIR/localhost.crt ]] && return
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=jdh test CA" \
        -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -keyout "$DIR/ca.key" -out "$DIR/ca.crt" 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" \
        -keyout "$DIR/localhost.key" -out "$DIR/localhost.csr" 2>/dev/null
    cat > "$DIR/ext.cnf" <<'EXT'
subjectAltName=DNS:localhost
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EXT
    openssl x509 -req -in "$DIR/localhost.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial \
        -days 365 -extfile "$DIR/ext.cnf" -out "$DIR/localhost.crt" 2>/dev/null
    chmod 644 "$DIR"/localhost.key  # read by the prosody user inside the container
}

case "${1:-}" in
start)
    certs
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" -p "127.0.0.1:$PORT:5222" \
        -v "$PWD/tools/prosody/prosody.cfg.lua:/etc/prosody/prosody.cfg.lua:ro" \
        -v "$PWD/$DIR/localhost.crt:/etc/prosody/certs/localhost.crt:ro" \
        -v "$PWD/$DIR/localhost.key:/etc/prosody/certs/localhost.key:ro" \
        "$IMAGE" >/dev/null
    for _ in $(seq 50); do
        docker exec "$NAME" prosodyctl status >/dev/null 2>&1 && break
        sleep 0.2
    done
    for u in "${USERS[@]}"; do
        docker exec "$NAME" prosodyctl register "$u" localhost "$PASSWORD" >/dev/null
    done
    echo "Prosody running on 127.0.0.1:$PORT, users: ${USERS[*]/%/@localhost} (password $PASSWORD)"
    echo "CA file: $DIR/ca.crt"
    ;;
stop)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped" || echo "not running"
    ;;
*)
    sed -n '2,7p' "$0"; exit 2 ;;
esac
