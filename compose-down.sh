#!/usr/bin/env bash
MACHINE_ID=$(cat .machine-id)
docker compose -f "docker-compose-${MACHINE_ID}.yml" --env-file="docker-compose-${MACHINE_ID}.env" down --remove-orphans
