#!/usr/bin/env bash

set -Eeuo pipefail
trap 'error "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
pushd "${SCRIPT_DIR}" >/dev/null

python3 -m venv venv
venv/bin/pip3 install --upgrade pip wheel setuptools
venv/bin/pip3 install --disable-pip-version-check --upgrade -r requirements-dev.txt

for subproject in ile_shellyscraper ile_tcp_cache qdb_count_rows qdb_wal_switch; do
  if [[ -f "$subproject/requirements.txt" ]]; then
    venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements.txt
  fi

  if [[ -f "$subproject/requirements-dev.txt" ]]; then
    venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements-dev.txt
  fi
done

popd >/dev/null
