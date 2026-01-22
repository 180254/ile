#!/usr/bin/env sh
# Mosquitto container entrypoint.
# Substitutes environment variables in config templates and starts Mosquitto.

set -xeu

envsubst < "/mosquitto/config/mosquitto.conf.tmpl" > "/mosquitto/config/mosquitto.conf"

mosquitto_passwd -b -c /mosquitto/secrets/passwd "${ILE_MQTT_USERNAME}" "${ILE_MQTT_PASSWORD}"
/usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
