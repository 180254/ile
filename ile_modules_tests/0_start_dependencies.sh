#!/bin/bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

ILE_TEST_MOSQUITTO_CONTAINER="ile-test-mosquitto"
ILE_TEST_QUESTDB_CONTAINER="ile-test-questdb"
ILE_TEST_VALKEY_CONTAINER="ile-test-valkey"
ILE_TEST_NETWORK="ile-test-network"

cleanup() {
  echo ""
  echo "Stopping dependencies..."

  docker stop "${ILE_TEST_MOSQUITTO_CONTAINER}" 2>/dev/null || true
  docker stop "${ILE_TEST_QUESTDB_CONTAINER}" 2>/dev/null || true
  docker stop "${ILE_TEST_VALKEY_CONTAINER}" 2>/dev/null || true

  docker rm "${ILE_TEST_MOSQUITTO_CONTAINER}" 2>/dev/null || true
  docker rm "${ILE_TEST_QUESTDB_CONTAINER}" 2>/dev/null || true
  docker rm "${ILE_TEST_VALKEY_CONTAINER}" 2>/dev/null || true

  docker network rm "${ILE_TEST_NETWORK}" 2>/dev/null || true

  echo "Dependencies stopped and removed."
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

echo "Creating Docker network: ${ILE_TEST_NETWORK}"
docker network create "${ILE_TEST_NETWORK}" 2>/dev/null || true

echo "Cleaning up any existing containers..."
docker rm -f "${ILE_TEST_MOSQUITTO_CONTAINER}" 2>/dev/null || true
docker rm -f "${ILE_TEST_QUESTDB_CONTAINER}" 2>/dev/null || true
docker rm -f "${ILE_TEST_VALKEY_CONTAINER}" 2>/dev/null || true

echo "Starting Mosquitto..."
docker run -d \
  --name "${ILE_TEST_MOSQUITTO_CONTAINER}" \
  --network "${ILE_TEST_NETWORK}" \
  -p "1883:1883" \
  eclipse-mosquitto:latest \
  mosquitto -c /mosquitto-no-auth.conf

echo "Starting QuestDB..."
docker run -d \
  --name "${ILE_TEST_QUESTDB_CONTAINER}" \
  --network "${ILE_TEST_NETWORK}" \
  -p "9000:9000" \
  -p "9009:9009" \
  -p "8812:8812" \
  -p "9003:9003" \
  questdb/questdb:latest

echo "Starting Valkey..."
docker run -d \
  --name "${ILE_TEST_VALKEY_CONTAINER}" \
  --network "${ILE_TEST_NETWORK}" \
  -p "6379:6379" \
  valkey/valkey:latest

echo ""
echo "Dependencies started:"
echo "  Mosquitto: 127.0.0.1:1883"
echo "  QuestDB:   127.0.0.1:9000 (Web Console & REST API)"
echo "             127.0.0.1:8812 (PostgreSQL wire protocol)"
echo "             127.0.0.1:9009 (InfluxDB line protocol)"
echo "             127.0.0.1:9003 (Health monitoring & Prometheus /metrics)"
echo "  Valkey:    127.0.0.1:6379"
echo ""
echo "Press Ctrl+C to stop all dependencies..."

while true; do
  sleep 1
done
