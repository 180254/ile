#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

if [ ! -f "${ILE_DIR}/.venv/bin/python3" ]; then
  echo "venv not found, run ${ILE_DIR}/extra-scripts/venv-create.sh first."
  exit
fi

function usage() {
  cat >&2 <<EOF
Usage: $0 <WAL>
EOF
}

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

if [ "$1" != "true" ] && [ "$1" != "false" ] && [ "$1" != "check" ]; then
  usage
  exit 1
fi

PYTHON3_BIN="${ILE_DIR}/.venv/bin/python3"

function get_env_var() {
  grep "$1" "$2" | cut -d '=' -f 2
}

QDB_PG_USER=$(get_env_var "QDB_PG_USER" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_PASSWORD=$(get_env_var "QDB_PG_PASSWORD" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_HOST=192.168.130.15
QDB_PG_PORT=8812

QDB_WAL=$1
QDB_DSN="postgresql://${QDB_PG_USER}:${QDB_PG_PASSWORD}@${QDB_PG_HOST}:${QDB_PG_PORT}/qdb" ${PYTHON3_BIN} qdb_wal_switch.py "${QDB_WAL}"

popd >/dev//null
