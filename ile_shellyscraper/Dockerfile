FROM python:3.11-bookworm AS build
RUN python3 -m venv /venv
RUN /venv/bin/pip3 install --upgrade pip setuptools wheel

FROM build AS build-venv
COPY requirements.txt /requirements.txt
RUN /venv/bin/pip3 install --disable-pip-version-check -r /requirements.txt

FROM python:3.11-slim-bookworm

RUN set -ex \
    && addgroup --system --gid 1001 nonroot \
    && adduser --system --uid 1001 --gid 1001 --no-create-home nonroot
USER nonroot

WORKDIR /app
COPY --from=build-venv /venv /venv
COPY shellyscraper.py /app

ENTRYPOINT ["/venv/bin/python3", "-u", "/app/shellyscraper.py"]
