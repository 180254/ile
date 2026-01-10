#!/bin/bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ILE_ROOT_DIR="$(dirname "${SCRIPT_DIR}")"

export ILE_DEBUG="${ILE_DEBUG:-false}"

export ILE_MQTT_HOST="127.0.0.1"
export ILE_MQTT_PORT="1883"
export ILE_MQTT_SSL="false"
export ILE_MQTT_USERNAME=""
export ILE_MQTT_PASSWORD=""

export ILE_VALKEY_HOST="127.0.0.1"
export ILE_VALKEY_PORT="6379"
export ILE_VALKEY_SSL="false"
export ILE_VALKEY_DB="0"
export ILE_VALKEY_PASSWORD=""

export PYTHONPATH="${ILE_ROOT_DIR}"
source "${ILE_ROOT_DIR}/.venv/bin/activate"

echo "Starting payload_normalizer..."
exec python3 -m ile_modules.payload_normalizer "$@"
