#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${ILE_DIR}" >/dev/null

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

BACKUPS_DIR="data_backups"
BACKUP_NAME="secrets_${TIMESTAMP}"
BACKUP_DIR="${BACKUPS_DIR}/${BACKUP_NAME}"
mkdir -p "$BACKUP_DIR"

function copy_directory() {
  local src=$1
  local dest=$2

  echo "Copying $src"
  cp -R "$src" "$dest" 2>/dev/null || echo "No $src to backup"
}

function copy_file() {
  local src=$1
  local dest=$2

  echo "Copying $src"
  cp "$src" "$dest" 2>/dev/null || echo "No $src to backup"
}

copy_directory "docker-compose/envs/" "$BACKUP_DIR"
copy_directory "docker-compose/tls/" "$BACKUP_DIR"
copy_file "extra-scripts/sync-servers.txt" "$BACKUP_DIR"
copy_file notepad.txt "$BACKUP_DIR"

pushd "${BACKUPS_DIR}" >/dev/null
tar --use-compress-program zstd -cf "${BACKUP_NAME}.tar.zst" "${BACKUP_NAME}/"
popd >/dev/null # was BACKUPS_DIR

echo "done. ${BACKUP_NAME}, compressed as ${BACKUP_NAME}.tar.zst"

popd >/dev/null # was ILE_DIR
