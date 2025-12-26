#!/usr/bin/env sh
set -xeu

envsubst < "/config/valkey.conf.tmpl" > "/config/valkey.conf"

for f in "/config/include/"*.tmpl; do
  [ -e "${f}" ] || break
  out="${f%.tmpl}"
  envsubst < "${f}" > "${out}"
done

exec valkey-server "/config/valkey.conf"
