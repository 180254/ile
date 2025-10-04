# https://github.com/redis/docker-library-redis/blob/release/8.2/debian/Dockerfile
FROM redis:8.2.2-bookworm

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN chown -R nonroot:nonroot /data

# IDR = ile dep redis
ARG ILE_IDR_REDIS_PASSWORD="redis"
ARG ILE_IDR_REDIS_PORT="6379"
ARG ILE_IDR_REDIS_SSL="false"
ARG ILE_IDR_REDIS_SSL_CERTFILE="redis.crt"
ARG ILE_IDR_REDIS_SSL_KEYFILE="redis.key"
ARG ILE_IDR_REDIS_SSL_PASSWORD=""

RUN mkdir -p /usr/local/etc/redis
RUN mkdir -p /usr/local/etc/redis/conf.d

# https://redis.io/docs/management/config-file/
COPY <<EOF /usr/local/etc/redis/redis.conf
bind * -::*
requirepass ${ILE_IDR_REDIS_PASSWORD}
dir /data
include /usr/local/etc/redis/conf.d/*.conf
EOF

COPY <<EOF /usr/local/etc/redis/conf.d/persistance.conf
save 3600 1 300 100 60 10000
appendonly yes
EOF

RUN if [ "$ILE_IDR_REDIS_SSL" = "false" ]; then \
  echo "port ${ILE_IDR_REDIS_PORT}" >> /usr/local/etc/redis/conf.d/port.conf; \
fi

RUN if [ "$ILE_IDR_REDIS_SSL" = "true" ]; then \
  echo "port 0" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-port ${ILE_IDR_REDIS_PORT}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-cert-file ${ILE_IDR_REDIS_SSL_CERTFILE}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-key-file ${ILE_IDR_REDIS_SSL_KEYFILE}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
  echo "tls-auth-clients no" >> /usr/local/etc/redis/conf.d/ssl.conf; \
fi

RUN if [ "$ILE_IDR_REDIS_SSL" = "true" ] && [ "${ILE_IDR_REDIS_SSL_PASSWORD}" != "" ]; then \
  echo "tls-key-file-pass ${ILE_IDR_REDIS_SSL_PASSWORD}" >> /usr/local/etc/redis/conf.d/ssl.conf; \
fi

USER nonroot

CMD [ "redis-server", "/usr/local/etc/redis/redis.conf" ]
