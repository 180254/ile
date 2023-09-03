# https://github.com/docker-library/redis/blob/master/7.0/Dockerfile

FROM redis:7.0.12

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN chown -R nonroot:nonroot /data

ARG ILR_REDIS_PASSWORD="redis"
ARG ILR_REDIS_PORT="6379"

ARG ILR_REDIS_SSL="false"
ARG ILR_REDIS_SSL_CERTFILE="redis.crt"
ARG ILR_REDIS_SSL_KEYFILE="redis.key"
ARG ILR_REDIS_SSL_PASSWORD=""

RUN mkdir -p /usr/local/etc/redis
RUN mkdir -p /usr/local/etc/redis/conf.d

# https://redis.io/docs/management/config-file/
COPY <<EOF /usr/local/etc/redis/redis.conf
bind * -::*
requirepass ${ILR_REDIS_PASSWORD}
dir /data
include /usr/local/etc/redis/conf.d/*.conf
EOF

COPY <<EOF /usr/local/etc/redis/conf.d/persistance.conf
save 3600 1 300 100 60 10000
appendonly yes
EOF

RUN if [ "$ILR_REDIS_SSL" = "false" ]; then \
  echo "port ${ILR_REDIS_PORT}" >> /usr/local/etc/redis/conf.d/port.conf; \
fi

RUN if [ "$ILR_REDIS_SSL" = "true" ]; then \
  echo "port 0" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-port ${ILR_REDIS_PORT}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-cert-file ${ILR_REDIS_SSL_CERTFILE}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-key-file ${ILR_REDIS_SSL_KEYFILE}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-auth-clients no" >> /usr/local/etc/redis/conf.d/ssl.conf; \
fi

RUN if [ "$ILR_REDIS_SSL" = "true" ] && [ "${ILR_REDIS_SSL_PASSWORD}" != "" ]; then \
  echo "tls-key-file-pass ${ILR_REDIS_SSL_PASSWORD}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
fi

USER nonroot

CMD [ "redis-server", "/usr/local/etc/redis/redis.conf" ]
