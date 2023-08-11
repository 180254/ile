#!/usr/bin/env bash

if [[ "$#" -lt 1 ]]; then
  >&2 echo "Usage: $0 [OPTIONS] COMMAND"
  exit 1
fi

MACHINE_ID=$(jq --arg hostname "$(hostname --fqdn)" '.[$hostname]' .machine-ids.json)
if [[ -z "${MACHINE_ID}" ]]; then
  >&2 echo "Error: machine id not set"
  exit 1
fi

COMMAND=("$@")
if [[ "${#COMMAND[@]}" -eq 1 ]]; then
  if [[ "${COMMAND[0]}" == "up" ]]; then
    COMMAND=("up" "-d" "--build" "--pull" "--remove-orphans")
  elif [[ "${COMMAND[0]}" == "down" ]]; then
    COMMAND=("down" "--remove-orphans")
  fi

fi

docker compose -f "docker-compose-${MACHINE_ID}.yml" --env-file="docker-compose-${MACHINE_ID}.env" "${COMMAND[@]}"
