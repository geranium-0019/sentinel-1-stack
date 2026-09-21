#!/usr/bin/env bash
set -euo pipefail
# Micromamba's entrypoint has set native-library data paths and user settings.
# Select the tested interpreter after activation, including env-python children.
export PATH="/usr/local/bin:${PATH}"
exec "$@"
