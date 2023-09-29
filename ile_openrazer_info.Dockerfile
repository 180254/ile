FROM debian:bookworm-slim

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

# python3 = 3.11 (https://packages.debian.org/bookworm/python3)
RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-openrazer \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

USER nonroot

WORKDIR /app
COPY ile_openrazer_info/openrazerinfo.py /app
COPY ile_shared_tools/*.py /app/ile_shared_tools/

ENTRYPOINT ["/usr/bin/python3", "-u", "/app/openrazerinfo.py"]
