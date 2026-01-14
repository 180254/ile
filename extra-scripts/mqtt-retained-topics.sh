#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

ILE_DIR=$(realpath "${SCRIPT_DIR}/../")
ENV_FILE="${ILE_DIR}/docker-compose/envs/prod/homeserver.env"
CA_CERT="${ILE_DIR}/docker-compose/tls/prod/ca.pem"

function get_env_var() {
  grep "$1" "$2" | cut -d '=' -f 2
}

ILE_MQTT_USERNAME=$(get_env_var "ILE_MQTT_USERNAME" "${ENV_FILE}")
ILE_MQTT_PASSWORD=$(get_env_var "ILE_MQTT_PASSWORD" "${ENV_FILE}")
MQTT_HOST="192.168.130.10"
MQTT_PORT="8883"

docker run --rm \
  --network ile-base_vnet \
  -v "${CA_CERT}:/ca.pem:ro" \
  eclipse-mosquitto:latest \
  mosquitto_sub \
  -d \
  -h "${MQTT_HOST}" \
  -p "${MQTT_PORT}" \
  --cafile /ca.pem \
  --tls-version tlsv1.3 \
  --insecure \
  -u "${ILE_MQTT_USERNAME}" \
  -P "${ILE_MQTT_PASSWORD}" \
  -t '#' \
  -v \
  --retained-only

popd >/dev/null
