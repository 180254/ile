#!/usr/bin/env bash
python3 -m venv venv
venv/bin/pip3 install --upgrade pip wheel setuptools
venv/bin/pip3 install --disable-pip-version-check --upgrade -r requirements.txt
