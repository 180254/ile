#!/usr/bin/env bash

if [[ "$#" -lt 2 ]]; then
  echo >&2 "Usage: $0 environment machine-types [OPTIONS] COMMAND"
  echo >&2 "  environment: local, prod"
  echo >&2 "  machine-types: $(find ./*.yml -printf "%f " | sed 's/\.yml//g')"

  # example:
  #   ./docker-compose.sh prod base,laptop up
  #   ./docker-compose.sh laptop,base down
  # example:
  #   ./docker-compose.sh prod base,cloudserver up
  #   ./docker-compose.sh prod cloudserver,base down
  # example:
  #   ./docker-compose.sh prod base,homeserver up
  #   ./docker-compose.sh prod homeserver,base down
  # example:
  #   ./docker-compose.sh local base,cloudserver,homeserver up
  #   ./docker-compose.sh local cloudserver,homeserver,base down

  exit 1
fi

ENVIRONMENT="$1"
MACHINE_TYPES="$2"
shift 2
COMMAND=("$@")

if [[ "${#COMMAND[@]}" -eq 1 ]]; then
  if [[ "${COMMAND[0]}" == "up" ]]; then
    COMMAND=("up" "-d" "--build" "--pull" "always" "--remove-orphans")
  elif [[ "${COMMAND[0]}" == "down" ]]; then
    COMMAND=("down" "--remove-orphans")
  fi
fi

for MACHINE_TYPE in $(echo "${MACHINE_TYPES}" | tr ',' '\n'); do
  PWD="$(realpath "${PWD}")" \
  ILEDIR=$(realpath "${PWD}/../") \
  HOSTNAME="$(hostname)" \
  ILE_NONROOT_UID="$(id -u)" \
  ILE_NONROOT_GID="$(id -g)" \
    docker compose -f "${MACHINE_TYPE}.yml" --env-file="envs/${ENVIRONMENT}/${MACHINE_TYPE}.env" "${COMMAND[@]}"
done
