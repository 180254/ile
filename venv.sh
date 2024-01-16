#!/usr/bin/env bash
python3 -m venv venv
venv/bin/pip3 install --upgrade pip wheel setuptools

venv/bin/pip3 install --disable-pip-version-check --upgrade -r requirements-dev.txt

for subproject in ile_shellyscraper ile_tcp_cache ile_openrazer_info qdb_count_rows qdb_wal_switch ile_weather_info; do
  if [[ -f "$subproject/requirements.txt" ]]; then
    venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements.txt
  fi

  if [[ -f "$subproject/requirements-dev.txt" ]]; then
    venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements-dev.txt
  fi
done
