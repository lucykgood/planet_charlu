#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="$PROJECT_ROOT/protos"
OUTPUT_DIR="$PROJECT_ROOT/src/planet_charlu/generated"

mkdir -p "$OUTPUT_DIR"
touch "$OUTPUT_DIR/__init__.py"

python -m grpc_tools.protoc \
  --proto_path="$PROTO_DIR" \
  --python_out="$OUTPUT_DIR" \
  "$PROTO_DIR/bazaar.proto"

echo "Generated Python protocol classes in $OUTPUT_DIR"