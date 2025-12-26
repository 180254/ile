#!/usr/bin/env sh
set -xeu

envsubst < "/config/redis.conf.tmpl" > "/config/redis.conf"

for f in "/config/include/"*.tmpl; do
  [ -e "${f}" ] || break
  out="${f%.tmpl}"
  envsubst < "${f}" > "${out}"
done

exec redis-server "/config/redis.conf"
