FROM python:3.13-trixie AS build-venv
RUN python3 -m venv /venv
RUN /venv/bin/pip3 install --upgrade pip setuptools wheel
COPY ile_shellyscraper/requirements.txt /requirements.txt
RUN /venv/bin/pip3 install --disable-pip-version-check -r /requirements.txt

FROM python:3.13-slim-trixie

ARG ILE_NONROOT_UID="1001"
ARG ILE_NONROOT_GID="1001"

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends socat \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -g "${ILE_NONROOT_GID}" nonroot \
  && useradd -l -u "${ILE_NONROOT_UID}" -g "${ILE_NONROOT_GID}" nonroot

USER nonroot

WORKDIR /app
COPY --from=build-venv /venv /venv
COPY ile_shellyscraper/shellyscraper.py /app
COPY ile_shared_tools/*.py /app/ile_shared_tools/

ENTRYPOINT ["/venv/bin/python3", "-u", "/app/shellyscraper.py"]
