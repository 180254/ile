#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${SCRIPT_DIR}" >/dev/null

servers=(
  "ip=srv1.example.com port=22 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=homeserver"
  "ip=srv2.example.com port=10392 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=cloudserver"
  "#ip=10.10.10.20 port=22 user=ubuntu key=~/.ssh/my.key path=/home/ubuntu/ile roles=laptop"
  "ip=localhost roles=laptop"
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
  "ile_shared_tools"
  "ile_shellyscraper"
  "ile_tcp_cache"
  "ile_telegraf"
  "qdb_benchmark"
  "qdb_count_rows"
  "qdb_wal_switch"
  "extra-scripts"
)

tar_zstd="sync.tar.zst"
rm -f "${tar_zstd}" || true
tar --use-compress-program zstd -cf "${tar_zstd}" "${paths[@]}" >/dev/null

for server in "${servers[@]}"; do
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

  if [[ "${ip}" == \#* ]]; then
    echo " > skipping ${ip}"
    continue
  fi

  if [[ "${ip}" == "localhost" ]]; then
    echo " > docker-compose on localhost"
    (cd "docker-compose" && ./docker-compose.sh prod "${roles},base" down)
    (cd "docker-compose" && ./docker-compose.sh prod "base,${roles}" up)
  else
    echo " > syncing with ${ip}"
    scp -P "${port}" -i "${key}" -r "${tar_zstd}" "${user}@${ip}:${path}"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path} && tar --use-compress-program zstd -xf ${tar_zstd} && rm ${tar_zstd}"

    echo "  > docker-compose on ${ip}"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod ${roles},base down"
    ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod base,${roles} up"
  fi
done

rm -f "${tar_zstd}"

popd >/dev/null
