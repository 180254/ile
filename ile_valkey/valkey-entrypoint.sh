#!/usr/bin/env sh
# Valkey container entrypoint.
# Substitutes environment variables in config templates and starts Valkey.

set -xeu

envsubst < "/valkey/config/valkey.conf.tmpl" > "/valkey/config/valkey.conf"

for f in "/valkey/config/include/"*.tmpl; do
  [ -e "${f}" ] || break
  out="${f%.tmpl}"
  envsubst < "${f}" > "${out}"
done

exec valkey-server "/valkey/config/valkey.conf"
