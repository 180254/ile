#!/usr/bin/env bash

ILE_DIR=$(realpath "${PWD}/../")

if [ ! -f "${ILE_DIR}/venv/bin/python3" ]; then
  echo "venv not found, run ${ILE_DIR}/venv.sh first."
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

if [ "$1" != "true" ] && [ "$1" != "false" ]; then
  usage
  exit 1
fi

PYTHON3_BIN="${ILE_DIR}/venv/bin/python3"

function get_env_var() {
  grep "$1" "$2" | cut -d '=' -f 2
}

QDB_PG_USER=$(get_env_var "QDB_PG_USER" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_PASSWORD=$(get_env_var "QDB_PG_PASSWORD" "${ILE_DIR}/docker-compose/envs/prod/homeserver.env")
QDB_PG_HOST=$(get_env_var "ILE_ITC_TARGET_TCP_HOST" "${ILE_DIR}/docker-compose/envs/prod/laptop.env")
QDB_PG_PORT=8812

QDB_WAL=$1
QDB_DSN="postgresql://${QDB_PG_USER}:${QDB_PG_PASSWORD}@${QDB_PG_HOST}:${QDB_PG_PORT}/qdb" ${PYTHON3_BIN} qdb_wal_switch.py "${QDB_WAL}"
