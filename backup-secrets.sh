#!/usr/bin/env bash

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

mkdir -p "000-backups/$TIMESTAMP"

echo "copying docker-compose/envs/prod"
cp -R docker-compose/envs/ "000-backups/$TIMESTAMP/" 2>/dev/null || echo "no envs/prod to backup"

echo "copying docker-compose/tls/prod"
cp -R docker-compose/tls/ "000-backups/$TIMESTAMP/" 2>/dev/null || echo "no tls/prod to backup"

echo "copying notepad.txt"
cp notepad.txt "000-backups/$TIMESTAMP/" 2>/dev/null || echo "no notepad to backup"

echo "backup created at 000-backups/$TIMESTAMP"
