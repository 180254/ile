#!/usr/bin/env bash

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

BACKUP_DIR="000-backups/$TIMESTAMP"
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
copy_file notepad.txt "$BACKUP_DIR"

echo "Backup created at $BACKUP_DIR"
