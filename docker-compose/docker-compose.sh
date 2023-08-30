#!/usr/bin/env bash

if [[ "$#" -lt 2 ]]; then
  >&2 echo "Usage: $0 machine-types [OPTIONS] COMMAND"
  >&2 echo "  machine-types: $(find ./*.yml -printf "%f " | sed 's/\.yml//g')"

  # example:
  #   ./docker-compose.sh base,laptop up
  #   ./docker-compose.sh laptop,base down
  # example:
  #   ./docker-compose.sh base,cloudserver up
  #   ./docker-compose.sh cloudserver,base down
  # example:
  #   ./docker-compose.sh base,homeserver up
  #   ./docker-compose.sh homeserver,base down
  # example:
  #   ./docker-compose.sh base,cloudserver,homeserver up
  #   ./docker-compose.sh cloudserver,homeserver,base down

  exit 1
fi

MACHINE_TYPES="$1"
shift

for MACHINE_TYPE in $(echo "${MACHINE_TYPES}" | tr ',' '\n'); do
  COMMAND=("$@")
  if [[ "${#COMMAND[@]}" -eq 1 ]]; then
    if [[ "${COMMAND[0]}" == "up" ]]; then
      COMMAND=("up" "-d" "--build" "--pull" "--remove-orphans")
    elif [[ "${COMMAND[0]}" == "down" ]]; then
      COMMAND=("down" "--remove-orphans")
    fi

  fi

  PWD="$(realpath "${PWD}/..")" \
    HOSTNAME="$(hostname)" \
    ILE_NONROOT_UID="$(id -u)" \
    ILE_NONROOT_GID="$(id -g)" \
    docker compose -f "${MACHINE_TYPE}.yml" --env-file="${MACHINE_TYPE}.env" "${COMMAND[@]}"
done

