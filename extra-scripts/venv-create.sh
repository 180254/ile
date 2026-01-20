#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ILE_DIR=$(realpath "${SCRIPT_DIR}/../")

pushd "${ILE_DIR}" >/dev/null

if ! command -v uv &>/dev/null; then
  echo "ERROR: 'uv' command not found (https://github.com/astral-sh/uv)." 1>&2
  exit 1
fi

VENV_PATH=".venv"

echo "Creating or upgrading virtual environment in: ${ILE_DIR}/${VENV_PATH}"
uv venv "${VENV_PATH}" --seed --allow-existing
uv pip install --python "${VENV_PATH}/bin/python3" --upgrade pip wheel setuptools
uv pip install --python "${VENV_PATH}/bin/python3" --upgrade -r requirements-dev.txt

for subproject in ile_modules qdb_scripts; do
  if [[ -f "$subproject/requirements.in" ]]; then
    echo "Installing requirements for subproject: $subproject"
    uv pip compile --python "${VENV_PATH}/bin/python3" --upgrade $subproject/requirements.in -o $subproject/requirements.txt
    uv pip install --python "${VENV_PATH}/bin/python3" -r $subproject/requirements.txt
  elif [[ -f "$subproject/requirements.txt" ]]; then
    echo "Installing requirements for subproject: $subproject"
    uv pip install --python "${VENV_PATH}/bin/python3" --upgrade -r $subproject/requirements.txt
  fi

  if [[ -f "$subproject/requirements-dev.in" ]]; then
    echo "Installing dev requirements for subproject: $subproject"
    uv pip install --python "${VENV_PATH}/bin/python3" --upgrade -r $subproject/requirements-dev.in
  fi
done

popd >/dev/null
