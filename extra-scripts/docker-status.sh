#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)

pushd "${SCRIPT_DIR}" >/dev/null

servers=(
  "ip=localhost"
)

if [ -f sync-servers.txt ]; then
  servers=()
  while IFS= read -r line; do
    servers+=("$line")
  done <sync-servers.txt
fi

popd >/dev/null

for server in "${servers[@]}"; do
  if [[ -z "${server// /}" ]]; then
    continue
  fi

  if [[ "${server}" == \#* ]]; then
    echo " > skipping ${server}"
    continue
  fi

  declare -A server_info
  for kv in $server; do
    IFS="=" read -r key value <<<"$kv"
    server_info[$key]=$value
  done

  ip="${server_info[ip]:-}"
  port="${server_info[port]:-}"
  user="${server_info[user]:-}"
  key="${server_info[key]:-}"

  if [[ "${ip}" == "localhost" ]]; then
    docker_output=$(docker ps -a --format '{{.Names}}|{{.Status}}')
  else
    docker_output=$(ssh -i "${key}" "${user}@${ip}" -p "${port}" 'docker ps -a --format "{{.Names}}|{{.Status}}"')
  fi

  while IFS='|' read -r name status; do
    printf "%-16s | %-48s | %s\n" "${ip}" "${name}" "${status}"
  done <<<"${docker_output}"
done
