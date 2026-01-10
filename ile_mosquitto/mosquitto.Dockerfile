# https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.22/docker/2.0-openssl/Dockerfile
FROM eclipse-mosquitto:2.0.22-openssl

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

USER root

RUN set -eux \
  && apk add --no-cache curl ca-certificates shadow \
  && /usr/sbin/groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && /usr/sbin/useradd -s /bin/sh -g "${ILE_NONROOT_GID}" -u "${ILE_NONROOT_UID}" nonroot

RUN set -eux \
    && mkdir -p /mosquitto/config \
    && mkdir -p /mosquitto/data \
    && mkdir -p /mosquitto/secrets \
    && chown -R nonroot:nonroot /mosquitto

COPY mosquitto-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER nonroot

ENTRYPOINT ["/entrypoint.sh"]
