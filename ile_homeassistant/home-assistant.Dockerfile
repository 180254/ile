# https://hub.docker.com/layers/homeassistant/home-assistant/2026.1.2/images/sha256-2db3ab13de2acf26a84de4b7488dfac85b3ea8fc9ebbfbbcd36540a24a802718
FROM ghcr.io/home-assistant/home-assistant:2026.1.2

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

USER root

RUN set -eux \
  && apk add --no-cache curl ca-certificates shadow \
  && /usr/sbin/groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && /usr/sbin/useradd -s /bin/sh -g "${ILE_NONROOT_GID}" -u "${ILE_NONROOT_UID}" nonroot

RUN set -eux \
    && mkdir -p /config \
    && chown -R nonroot:nonroot /config
