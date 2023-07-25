FROM python:3.11-bookworm AS build-venv
RUN python3 -m venv /venv
RUN /venv/bin/pip3 install --upgrade pip setuptools wheel
COPY requirements.txt /requirements.txt
RUN /venv/bin/pip3 install --disable-pip-version-check -r /requirements.txt

FROM python:3.11-slim-bookworm

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
    && addgroup --system --gid "${ILE_NONROOT_GID}" nonroot \
    && adduser --system --uid "${ILE_NONROOT_UID}" --gid "${ILE_NONROOT_GID}" --no-create-home nonroot
USER nonroot

WORKDIR /app
COPY --from=build-venv /venv /venv
COPY tcpcache.py /app

ENTRYPOINT ["/venv/bin/python3", "-u", "/app/tcpcache.py"]
