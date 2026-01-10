# https://github.com/questdb/questdb/blob/9.2.3/core/Dockerfile
FROM questdb/questdb:9.3.0

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && microdnf install -y curl ca-certificates shadow-utils \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot \
  && microdnf remove -y shadow-utils \
  && microdnf clean all

RUN chown -R nonroot:nonroot /var/lib/questdb

USER nonroot

ENTRYPOINT ["/docker-entrypoint.sh"]
