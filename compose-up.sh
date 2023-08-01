#!/usr/bin/env bash
MACHINE_ID=$(cat .machine-id)
docker compose -f "docker-compose-${MACHINE_ID}.yml" --env-file="docker-compose-${MACHINE_ID}.env" up -d --build --pull --remove-orphans
