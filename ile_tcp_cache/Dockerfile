FROM python:3.11-bookworm AS build-venv
RUN python3 -m venv /venv
RUN /venv/bin/pip3 install --upgrade pip setuptools wheel
COPY requirements.txt /requirements.txt
RUN /venv/bin/pip3 install --disable-pip-version-check -r /requirements.txt

FROM python:3.11-slim-bookworm

# useful for healthchecks
RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends socat \
  && rm -rf /var/lib/apt/lists/*

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
    && groupadd -g "${ILE_NONROOT_GID}" nonroot \
    && useradd -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot
USER nonroot

WORKDIR /app
COPY --from=build-venv /venv /venv
COPY tcpcache.py /app

ENTRYPOINT ["/venv/bin/python3", "-u", "/app/tcpcache.py"]
