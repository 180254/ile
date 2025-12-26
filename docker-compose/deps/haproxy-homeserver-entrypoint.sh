#!/usr/bin/env sh
set -xeu

envsubst < "/config/haproxy.cfg.tmpl" > "/config/haproxy.cfg"
exec haproxy -f "/config/haproxy.cfg"
