# https://github.com/valkey-io/valkey-container/blob/mainline/9.0/debian/Dockerfile
FROM valkey/valkey:9.0.1-trixie

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates gettext-base \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN set -eux \
  && mkdir -p /valkey/data \
  && mkdir -p /valkey/config \
  && mkdir -p /valkey/config/include \
  && chown -R nonroot:nonroot /valkey

COPY valkey-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER nonroot

ENTRYPOINT ["/entrypoint.sh"]
