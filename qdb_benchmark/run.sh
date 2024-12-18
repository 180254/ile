#!/usr/bin/env bash

set -Eeuo pipefail
trap 'error "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

if [ ! -f "${ILE_DIR}/venv/bin/python3" ]; then
  echo "venv not found, run ${ILE_DIR}/venv.sh first."
  exit
fi

PYTHON3_BIN="${ILE_DIR}/venv/bin/python3"

function get_env_var() {
  grep "$1" "$2" | cut -d '=' -f 2
}

QDB_PG_USER=$(get_env_var "QDB_PG_USER" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_PASSWORD=$(get_env_var "QDB_PG_PASSWORD" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_HOST=$(get_env_var "ILE_ITC_TARGET_TCP_HOST" "${ILE_DIR}/docker-compose/envs/prod/laptop.env")
QDB_PG_PORT=8812

QDB_DSN="postgresql://${QDB_PG_USER}:${QDB_PG_PASSWORD}@${QDB_PG_HOST}:${QDB_PG_PORT}/qdb" ${PYTHON3_BIN} qdb_benchmark.py

popd >/dev//null
