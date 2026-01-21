# https://github.com/Koenkk/zigbee2mqtt/blob/2.7.2/docker/Dockerfile
FROM ghcr.io/koenkk/zigbee2mqtt:2.7.2

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apk add --no-cache shadow \
  && /usr/sbin/groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && /usr/sbin/useradd -s /bin/sh -g "${ILE_NONROOT_GID}" -u "${ILE_NONROOT_UID}" nonroot \
  && apk del shadow

RUN set -eux \
  && mkdir -p /app/data \
  && chown -R nonroot:nonroot /app

USER nonroot
