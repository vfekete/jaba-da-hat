#!/usr/bin/env bash
# Start jaba-da-hat from source inside the project's virtual environment (for manual testing).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
uv sync --quiet
# shellcheck disable=SC1091
source .venv/bin/activate
exec python -m jaba_da_hat "$@"
