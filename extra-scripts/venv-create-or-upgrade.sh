#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${ILE_DIR}" >/dev/null

command -v uv &>/dev/null && TOOL=uv || TOOL=
echo "Using tool: ${TOOL:-python3 -m venv}"

echo "Creating or upgrading virtual environment in: ${ILE_DIR}/venv"
if [[ "${TOOL}" == "uv" ]]; then
  uv venv venv --seed --allow-existing
  uv pip install --python venv/bin/python3 --upgrade pip wheel setuptools
  uv pip install --python venv/bin/python3 --upgrade -r requirements-dev.txt
else
  python3 -m venv venv
  venv/bin/pip3 install --upgrade pip wheel setuptools
  venv/bin/pip3 install --disable-pip-version-check --upgrade -r requirements-dev.txt
fi

for subproject in ile_shellyscraper ile_tcp_cache qdb_count_rows qdb_wal_switch; do
  if [[ -f "$subproject/requirements.txt" ]]; then
    echo "Installing requirements for subproject: $subproject"
    if [[ "${TOOL}" == "uv" ]]; then
      uv pip install --python venv/bin/python3 --upgrade -r $subproject/requirements.txt
    else
      venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements.txt
    fi
  fi

  if [[ -f "$subproject/requirements-dev.txt" ]]; then
    echo "Installing dev requirements for subproject: $subproject"
    if [[ "${TOOL}" == "uv" ]]; then
      uv pip install --python venv/bin/python3 --upgrade -r $subproject/requirements.txt
    else
      venv/bin/pip3 install --disable-pip-version-check --upgrade -r $subproject/requirements-dev.txt
    fi
  fi
done

popd >/dev/null
