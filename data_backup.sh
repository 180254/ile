#!/usr/bin/env bash

TIMESTAMP=$(date --iso-8601=seconds)
TIMESTAMP=${TIMESTAMP//:/}

if [[ "$(docker ps -q -f name=ile-)" ]]; then
  echo "ile is running, please stop it first"
  exit 1
fi

for dir in data_*; do
  if [[ ! -d "${dir}" ]]; then
    continue
  fi

  DIR_SIZE=$(du -sh "${dir}" | cut -f1)
  DIR_SIZE_BYTES=$(du -sb "${dir}" | cut -f1)

  BASENAME="$(basename "${dir}")"
  BACKUP_NAME="${BASENAME}_${TIMESTAMP}.tar.zst"

  echo "processing ${BASENAME}"
  #tar --zstd -cf "${BACKUP_NAME}" "${dir}"
  tar -cf - "${dir}" | pv -s "${DIR_SIZE_BYTES}" | zstd -3 -T0 -q - -o "${BACKUP_NAME}"

  BACKUP_SIZE=$(du -sh "${BACKUP_NAME}" | cut -f1)
  echo "backed up ${BASENAME}=$DIR_SIZE $BACKUP_NAME=$BACKUP_SIZE"
done
