#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

function usage() {
  cat >&2 <<EOF
Usage: $0 environment machine-types [OPTIONS] COMMAND
  environment: local, prod
  machine-types: $(find ./*.yml -printf "%f " | sed 's/\.yml//g')
  command: up, down, ...

Examples:
  ./docker-compose.sh prod base,laptop up
  ./docker-compose.sh prod laptop,base down
  ./docker-compose.sh prod base,cloudserver up
  ./docker-compose.sh prod cloudserver,base down
  ./docker-compose.sh prod base,homeserver up
  ./docker-compose.sh prod homeserver,base down
  ./docker-compose.sh local base,cloudserver,homeserver up
  ./docker-compose.sh local cloudserver,homeserver,base down
EOF
}
if [[ "$#" -lt 2 ]]; then
  usage
  exit 1
fi

ENVIRONMENT="$1"
MACHINE_TYPES="$2"
shift 2
COMMAND=("$@")

if [[ "${#COMMAND[@]}" -eq 1 ]]; then
  case "${COMMAND[0]}" in
  up)
    COMMAND=("up" "-d" "--build" "--pull" "always" "--remove-orphans")
    ;;
  down)
    COMMAND=("down" "--remove-orphans")
    ;;
  esac
fi

ILE_DIR=$(realpath "${PWD}/../")

function create_directories() {
  local type=$1
  case "$type" in
  cloudserver)
    mkdir -p "${ILE_DIR}/data_redis"
    ;;
  homeserver)
    mkdir -p "${ILE_DIR}/data_questdb" "${ILE_DIR}/data_grafana"
    ;;
  laptop)
    mkdir -p "${ILE_DIR}/data_redis_laptop"
    ;;
  esac
}

IFS=',' read -ra TYPES <<<"$MACHINE_TYPES"

for TYPE in "${TYPES[@]}"; do
  create_directories "$TYPE"
done

for MACHINE_TYPE in "${TYPES[@]}"; do
  PWD="$(realpath "${PWD}")" \
  ILE_DIR="${ILE_DIR}" \
  HOSTNAME="$(hostname)" \
  ILE_NONROOT_UID="$(id -u)" \
  ILE_NONROOT_GID="$(id -g)" \
    docker compose -f "${MACHINE_TYPE}.yml" --env-file="envs/${ENVIRONMENT}/${MACHINE_TYPE}.env" "${COMMAND[@]}"
done

popd >/dev/null
