#!/usr/bin/env sh
# Mosquitto container entrypoint.
# Creates password file from environment variables and starts Mosquitto.

set -xeu

mosquitto_passwd -b -c /mosquitto/secrets/passwd "${ILE_MQTT_USERNAME}" "${ILE_MQTT_PASSWORD}"
/usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
