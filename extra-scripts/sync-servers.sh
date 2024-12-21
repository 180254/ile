#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${SCRIPT_DIR}" >/dev/null

servers=(
  "srv1.example.com 22 ubuntu ~/.ssh/my.key /home/ubuntu/ile homeserver"
  "srv2.example.com 10392 ubuntu ~/.ssh/my.key /home/ubuntu/ile cloudserver"
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
  IFS=" " read -r -a server <<<"$server"
  ip="${server[0]}"
  port="${server[1]}"
  user="${server[2]}"
  key="${server[3]}"
  path="${server[4]}"
  roles="${server[5]}"

  echo " > syncing with ${ip}"
  scp -P "${port}" -i "${key}" -r "${tar_zstd}" "${user}@${ip}:${path}"
  ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path} && tar --use-compress-program zstd -xf ${tar_zstd} && rm ${tar_zstd}"

  echo "  > docker-compose on ${ip}"
  ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod ${roles},base down"
  ssh -i "${key}" "${user}@${ip}" -p "${port}" "cd ${path}/docker-compose && ./docker-compose.sh prod base,${roles} up"
done

rm -f "${tar_zstd}"

echo " > docker-compose on localhost"
(cd "docker-compose" && ./docker-compose.sh prod laptop,base down)
(cd "docker-compose" && ./docker-compose.sh prod base,laptop up)
pwd

popd >/dev/null
