#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "ERROR: ${BASH_SOURCE:-$BASH_COMMAND in $0}: ${FUNCNAME[0]:-line} at line: $LINENO, arguments: $*" 1>&2; exit 1' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
AWK_SCRIPT=uevent_to_logfmt.awk
POWER_SUPPLY_DIR=/sys/class/power_supply

for d in "$POWER_SUPPLY_DIR"/*; do
  uevent="$d/uevent"
  [ -f "$uevent" ] || continue
  awk -f "$SCRIPT_DIR/$AWK_SCRIPT" "$uevent"
done
