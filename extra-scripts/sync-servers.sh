#!/usr/bin/env bash
# Sync ILE codebase to remote servers and deploy containers.
# Reads server list from sync-servers.txt or uses defaults.
# Set SOFTSYNC=true to skip docker-compose commands.

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${SCRIPT_DIR}" >/dev/null

SOFTSYNC="${SOFTSYNC:-false}"

servers=(
  "ip=srv1.example.com port=22 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=homeserver"
  "ip=srv2.example.com port=10392 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=cloudserver"
  "#ip=10.10.10.20 port=22 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=laptop"
  "ip=localhost roles=laptop path=/home/ubuntu/ile/.on_air"
)

if [ -f sync-servers.txt ]; then
  servers=()
  while IFS= read -r line; do
    servers+=("$line")
  done <sync-servers.txt
fi

popd >/dev/null
pushd "${ILE_DIR}" >/dev/null

paths=(
  "docker-compose"
  "extra-scripts"
  "ile_grafana"
  "ile_homeassistant"
  "ile_haproxy"
  "ile_modules"
  "ile_modules_tests"
  "ile_mosquitto"
  "ile_questdb"
  "ile_telegraf"
  "ile_valkey"
  "ile_zigbee2mqtt"
  "qdb_scripts"
  "requirements-dev.txt"
)

tar_zstd="sync.tar.zst"
rm -f "${tar_zstd}" || true
tar --use-compress-program zstd -cf "${tar_zstd}" "${paths[@]}" >/dev/null

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
  path="${server_info[path]:-}"
  roles="${server_info[roles]:-}"

  if [[ "${ip}" == "localhost" ]]; then
    echo " > syncing localhost"

    if [ "${SOFTSYNC}" != "false" ]; then
      echo "  SOFTSYNC SYNC enabled, skipping localhost sync"
      continue
    fi

    echo " > syncing localhost"
    mkdir -p "${path}"
    cp "${tar_zstd}" "${path}"
    pushd "${path}" >/dev/null
    tar --use-compress-program zstd -xf "${tar_zstd}"

    echo " > docker-compose on localhost > build"
    (cd "docker-compose" && ./docker-compose.sh prod "${roles},base" build)

    echo " > docker-compose on localhost > down"
    (cd "docker-compose" && ./docker-compose.sh prod "${roles},base" down)

    echo " > docker-compose on localhost > up"
    (cd "docker-compose" && ./docker-compose.sh prod "base,${roles}" up)

    popd >/dev/null

  else
    echo " > syncing ${ip}"
    scp -P "${port}" -i "${key}" -r "${tar_zstd}" "${user}@${ip}:${path}"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path} && tar --use-compress-program zstd -xf ${tar_zstd} && rm ${tar_zstd}"

    if [ "${SOFTSYNC}" != "false" ]; then
      echo "  SOFTSYNC enabled, skipping remote docker-compose commands on ${ip}"
      continue
    fi

    echo " > docker-compose on ${ip} > build"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod ${roles},base build"

    echo " > docker-compose on ${ip} > down"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod ${roles},base down"

    echo " > docker-compose on ${ip} > up"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod base,${roles} up"
  fi
done

rm -f "${tar_zstd}"

popd >/dev/null
