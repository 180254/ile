#!/bin/bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ILE_ROOT_DIR="$(dirname "${SCRIPT_DIR}")"

export ILE_DEBUG="${ILE_DEBUG:-false}"

export ILE_IQW_VALKEY_HOST="127.0.0.1"
export ILE_IQW_VALKEY_PORT="6379"
export ILE_IQW_VALKEY_SSL="false"
export ILE_IQW_VALKEY_DB="0"
export ILE_IQW_VALKEY_PASSWORD=""

export ILE_IQW_QUESTDB_HOST="127.0.0.1"
export ILE_IQW_QUESTDB_PORT="9000"
export ILE_IQW_QUESTDB_SSL="false"

export PYTHONPATH="${ILE_ROOT_DIR}"
source "${ILE_ROOT_DIR}/.venv/bin/activate"

echo "Starting questdb_writer..."
exec python3 -m ile_modules.questdb_writer "$@"
