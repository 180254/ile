# https://github.com/docker-library/haproxy/blob/master/3.3/Dockerfile
FROM haproxy:3.3.2-trixie

USER root

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl gettext-base \
  && rm -rf /var/lib/apt/lists/*

RUN set -eux \
    && mkdir -p /config \
    && chown -R haproxy:haproxy /config

COPY haproxy-entrypoint.sh /entrypoint.sh
RUN set -eux \
  && chmod +x /entrypoint.sh

USER haproxy

ENTRYPOINT ["/entrypoint.sh"]
