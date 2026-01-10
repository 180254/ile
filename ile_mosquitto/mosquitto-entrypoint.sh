#!/usr/bin/env sh

set -xeu

mosquitto_passwd -b -c /mosquitto/secrets/passwd "${ILE_MQTT_USERNAME}" "${ILE_MQTT_PASSWORD}"
/usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
