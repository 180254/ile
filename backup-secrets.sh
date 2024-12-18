#!/usr/bin/env bash

set -Eeuo pipefail
trap 'error "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

BACKUP_DIR="data_backups/$TIMESTAMP"
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
copy_directory "scripts" "$BACKUP_DIR"
copy_file notepad.txt "$BACKUP_DIR"

echo "Backup created at $BACKUP_DIR"

popd >/dev/null
