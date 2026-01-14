#!/usr/bin/env sh
# HAProxy container entrypoint.
# Substitutes environment variables in config template and starts HAProxy.

set -xeu

envsubst < "/config/haproxy.cfg.tmpl" > "/config/haproxy.cfg"
exec haproxy -f "/config/haproxy.cfg"
