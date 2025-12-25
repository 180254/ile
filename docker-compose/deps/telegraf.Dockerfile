# https://github.com/influxdata/influxdata-docker/blob/master/telegraf/1.37/Dockerfile
FROM telegraf:1.37.0

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

USER nonroot

CMD [ "telegraf", "--config", "/etc/telegraf/telegraf.conf", "--config-directory", "/etc/telegraf/telegraf.d" ]
