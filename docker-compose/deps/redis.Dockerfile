# https://github.com/redis/docker-library-redis/blob/release/8.4/debian/Dockerfile
FROM redis:8.4.0-bookworm

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates gettext-base \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN set -eux \
  && mkdir -p /data \
  && mkdir -p /config \
  && mkdir -p /config/include \
  && chown -R nonroot:nonroot /data \
  && chown -R nonroot:nonroot /config \
  && chown -R nonroot:nonroot /config/include

COPY deps/redis-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER nonroot

ENTRYPOINT ["/entrypoint.sh"]
