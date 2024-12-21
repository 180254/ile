#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${ILE_DIR}" >/dev/null

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

BACKUP_DIR="data_backups"

if [[ "$(docker ps -q -f name=ile-)" ]]; then
  echo "ile is running, please stop it first"
  exit 1
fi

for dir in data_*; do
  if [[ ! -d "${dir}" ]]; then
    continue
  fi
  if [[ "${dir}" == "${BACKUP_DIR}" ]]; then
    continue
  fi

  DIR_SIZE=$(du -sh "${dir}" | cut -f1)
  DIR_SIZE_BYTES=$(du -sb "${dir}" | cut -f1)

  BASENAME="$(basename "${dir}")"
  BACKUP_NAME="${BASENAME}_${TIMESTAMP}.tar.zst"
  BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

  echo "processing ${BASENAME}"
  #tar --zstd -cf "${BACKUP_NAME}" "${dir}"
  tar -cf - "${dir}" | pv -s "${DIR_SIZE_BYTES}" | zstd -3 -T0 -q - -o "${BACKUP_PATH}"

  BACKUP_SIZE=$(du -sh "${BACKUP_PATH}" | cut -f1)
  echo "${BASENAME}=${DIR_SIZE} ${BACKUP_NAME}=${BACKUP_SIZE}"
done

popd >/dev/null
