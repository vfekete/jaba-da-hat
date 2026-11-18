#!/usr/bin/env bash
# Check the built binary as if on a brand-new user account: empty HOME, minimal environment,
# no session bus, offscreen Qt. Nothing outside a temporary directory is touched.
#   tools/package-check.sh            uses build/jdh-<version from pyproject.toml>
#   tools/package-check.sh <binary>
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
VERSION=$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)
BIN=$(readlink -f "${1:-build/jdh-$VERSION}")
[[ -x $BIN ]] || { echo "no binary at $BIN — run ./build.sh first" >&2; exit 2; }

T=$(mktemp -d "${TMPDIR:-/tmp}/jdh-clean.XXXXXX")
H=$T/home
mkdir -p "$H" "$T/tmp"
cleanup() { for p in $(ours); do kill -TERM "$p" 2>/dev/null; done; rm -rf "$T"; }
trap cleanup EXIT
APPS=$H/.local/share/applications/jaba-da-hat.desktop
AUTO=$H/.config/autostart/jaba-da-hat.desktop
FAILS=0
ok()   { printf '  \e[32mok\e[0m   %s\n' "$1"; }
bad()  { printf '  \e[31mFAIL\e[0m %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
ENV=(env -i HOME="$H" PATH=/usr/bin:/bin TMPDIR="$T/tmp" LANG=C.UTF-8 QT_QPA_PLATFORM=offscreen)
run() { "${ENV[@]}" "$BIN" "$@"; }
start_stop() {  # start like a login session would, let it settle, then end it with SIGTERM like a logout
    # exec: $! must be the app itself, not a wrapper subshell, or SIGTERM never reaches it
    (exec "${ENV[@]}" "$BIN" "$@") >"$T/run.log" 2>&1 &
    local pid=$!
    sleep "${WAIT:-6}"
    kill -TERM "$pid" 2>/dev/null
    wait "$pid"
}
ours() {  # PIDs of processes started by this check (matched by their HOME)
    for p in $(pgrep -f -- "$BIN" || true); do
        tr '\0' '\n' <"/proc/$p/environ" 2>/dev/null | grep -qx "HOME=$H" && echo "$p"
    done
}
field() { sed -n "s/^$1=//p" "$2" 2>/dev/null | head -1; }

echo "binary: $BIN ($VERSION)"
echo "1. --version"
check "prints $VERSION" '[[ "$(run --version 2>&1)" == *"$VERSION"* ]]'

echo "2. first start on an empty account"
start_stop; rc=$?
check "quits cleanly on SIGTERM (exit $rc)" '[[ $rc -eq 0 ]]'
check "no process left behind" '[[ -z "$(ours)" ]]'
check "launcher registered" '[[ -f "$APPS" ]]'
check "launcher Exec is this binary" '[[ "$(field Exec "$APPS")" == "$BIN" ]]'
check "launcher icon exists" '[[ -f "$(field Icon "$APPS")" ]]'
check "launcher records version $VERSION" '[[ "$(field X-AppVersion "$APPS")" == "$VERSION" ]]'
check "autostart entry created (start at login is the default)" '[[ -f "$AUTO" ]]'
check "autostart Exec is this binary --autostart" '[[ "$(field Exec "$AUTO")" == "$BIN --autostart" ]]'
if command -v desktop-file-validate >/dev/null; then
    check "launcher passes desktop-file-validate" 'desktop-file-validate "$APPS" >/dev/null 2>&1'
    check "autostart passes desktop-file-validate" 'desktop-file-validate "$AUTO" >/dev/null 2>&1'
fi
check "settings dir is private (0700)" '[[ "$(stat -c %a "$H/.config/jaba-da-hat")" == 700 ]]'
check "settings file is private (0600)" '[[ "$(stat -c %a "$H/.config/jaba-da-hat/config.json")" == 600 ]]'
check "unpacked into ~/.cache/jaba-da-hat/$VERSION" '[[ -d "$H/.cache/jaba-da-hat/$VERSION" ]]'
check "no Python traceback in the log" '! grep -q Traceback "$T/run.log"'
WAIT=5 start_stop --open settings  # loads QML windows: a style or file missing from the binary shows up here
check "QML windows load (no QML errors)" '! grep -qE "ERROR jaba_da_hat.core|Component is not ready|\\.qml:[0-9]+:[0-9]+: .*(not installed|is not a type|Non-existent)" "$T/run.log"'

echo "3. start at login (as the session would run it)"
WAIT=4 start_stop --autostart; rc=$?
check "autostart run quits cleanly (exit $rc)" '[[ $rc -eq 0 ]]'

echo "4. an older version is registered"
sed -i "s/^X-AppVersion=.*/X-AppVersion=0.0.1/" "$APPS"
sed -i "s|^Exec=.*|Exec=/old/place/jdh-0.0.1 --autostart|" "$AUTO"
WAIT=4 start_stop
check "launcher re-registered to $VERSION" '[[ "$(field X-AppVersion "$APPS")" == "$VERSION" ]]'
check "autostart now points at this binary" '[[ "$(field Exec "$AUTO")" == "$BIN --autostart" ]]'

echo "5. a newer version is registered"
sed -i "s/^X-AppVersion=.*/X-AppVersion=99.0.0/" "$APPS"
WAIT=4 start_stop
check "newer registration left alone" '[[ "$(field X-AppVersion "$APPS")" == "99.0.0" ]]'

echo "6. --unregister"
run --unregister >/dev/null 2>&1
check "launcher removed" '[[ ! -e "$APPS" ]]'
check "autostart entry removed" '[[ ! -e "$AUTO" ]]'
check "icons removed" '[[ -z "$(find "$H/.local/share/icons" -name "jaba-da-hat*" 2>/dev/null)" ]]'
check "settings kept (user data)" '[[ -f "$H/.config/jaba-da-hat/config.json" ]]'

echo "7. explicit --register"
run --register >/dev/null 2>&1
check "launcher registered again" '[[ -f "$APPS" ]]'
check "no autostart from --register alone" '[[ ! -e "$AUTO" ]]'

echo "files the app created in the empty home:"
(cd "$H" && find . -type f ! -path "./.cache/jaba-da-hat/*" | sort | sed 's/^/  /')
if [[ $FAILS -eq 0 ]]; then echo "all checks passed"; else echo "$FAILS check(s) failed"; fi
exit $((FAILS > 0))
