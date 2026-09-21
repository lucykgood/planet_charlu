#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHITECTURE="$(uname -m)"

case "$ARCHITECTURE" in
    arm64|aarch64)
        VALIDATOR="$PROJECT_ROOT/validator/spaceport-validate-linux-arm64"
        ;;
    x86_64|amd64)
        VALIDATOR="$PROJECT_ROOT/validator/spaceport-validate-linux-x86_64"
        ;;
    *)
        echo "Unsupported architecture: $ARCHITECTURE" >&2
        exit 1
        ;;
esac

if [[ ! -f "$VALIDATOR" ]]; then
    echo "Validator binary not found: $VALIDATOR" >&2
    exit 1
fi

if [[ ! -x "$VALIDATOR" ]]; then
    chmod +x "$VALIDATOR"
fi

exec "$VALIDATOR" --codec protobuf "$@"