#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

./scripts/generate_proto.sh

PYTHONPATH=src python -c "
from planet_charlu.generated import bazaar_pb2
print('Successfully imported bazaar_pb2')
"

echo "Protobuf generation and import verification passed."