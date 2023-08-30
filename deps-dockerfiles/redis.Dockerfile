# https://github.com/docker-library/redis/blob/master/7.0/Dockerfile

FROM redis:7.0.12

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

ARG ILE_REDIS_PASSWORD="redis"
ARG ILE_REDIS_PORT="6379"

RUN mkdir -p /usr/local/etc/redis

# https://redis.io/docs/management/config-file/
COPY <<EOF /usr/local/etc/redis/redis.conf
port ${ILE_REDIS_PORT}
bind * -::*
save 3600 1 300 100 60 10000
appendonly yes
requirepass ${ILE_REDIS_PASSWORD}
dir /data
EOF

RUN set -eux \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN chown -R nonroot:nonroot /data

USER nonroot

CMD [ "redis-server", "/usr/local/etc/redis/redis.conf" ]
