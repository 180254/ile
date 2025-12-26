#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${ILE_DIR}" >/dev/null

if [[ "$(docker ps -q -f name=ile-)" ]]; then
  echo "ile is running, please stop it first"
  exit 1
fi

rm -rf data_grafana/
rm -rf data_questdb/
rm -rf data_valkey_cloudserver/
rm -rf data_valkey_laptop/

mkdir data_grafana/
mkdir data_questdb/
mkdir data_valkey_cloudserver/
mkdir data_valkey_laptop/

touch data_grafana/.keep
touch data_questdb/.keep
touch data_valkey_cloudserver/.keep
touch data_valkey_laptop/.keep

popd >/dev/null

echo "OK"
