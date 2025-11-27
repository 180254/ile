# https://github.com/questdb/questdb/blob/9.2.1/core/Dockerfile
FROM questdb/questdb:9.2.1

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN chown -R nonroot:nonroot /var/lib/questdb

USER nonroot

ENTRYPOINT ["/docker-entrypoint.sh"]
