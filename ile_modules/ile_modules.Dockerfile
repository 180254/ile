FROM python:3.14-trixie AS build-venv

COPY requirements.txt /requirements.txt

RUN python3 -m venv /.venv
RUN /.venv/bin/pip3 install --upgrade pip setuptools wheel
RUN /.venv/bin/pip3 install --disable-pip-version-check -r /requirements.txt

FROM python:3.14-slim-trixie

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

USER nonroot

COPY --from=build-venv /.venv /.venv
COPY ile_tools.py /app/ile_modules/ile_tools.py
COPY mqtt_ingestor.py /app/ile_modules/mqtt_ingestor.py
COPY payload_normalizer.py /app/ile_modules/payload_normalizer.py
COPY questdb_writer.py /app/ile_modules/questdb_writer.py
COPY report_printer.py /app/ile_modules/report_printer.py

WORKDIR /app

ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# /.venv/bin/python3 -m ile_modules.mqtt_ingestor
