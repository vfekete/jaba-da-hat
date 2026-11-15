#!/usr/bin/env bash
# Check every locked dependency (incl. build and dev groups) for known vulnerabilities (CVE / GHSA / PYSEC).
# Uses pip-audit in a throw-away uv tool environment; only package names and versions are sent (OSV + PyPI).
# Exit code 1 when anything is found — build.sh stops then.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
REQ=$(mktemp)
trap 'rm -f "$REQ"' EXIT
uv export --quiet --format requirements-txt --no-hashes --no-emit-project --all-groups > "$REQ"
for service in osv pypi; do
    uvx --quiet pip-audit --disable-pip --no-deps --progress-spinner off \
        --vulnerability-service "$service" -r "$REQ" 2> >(grep -v "^WARNING" >&2)
done
