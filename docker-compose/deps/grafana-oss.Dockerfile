# https://github.com/grafana/grafana/blob/v10.2.3/Dockerfile
FROM grafana/grafana-oss:11.3.1-ubuntu

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

USER root

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

RUN chown -R nonroot:nonroot \
  /etc/grafana/grafana.ini \
  /var/lib/grafana \
  /usr/share/grafana \
  /var/log/grafana \
  /var/lib/grafana/plugins \
  /etc/grafana/provisioning

USER nonroot

ENTRYPOINT [ "/run.sh" ]
