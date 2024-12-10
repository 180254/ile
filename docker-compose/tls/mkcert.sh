#!/bin/bash

if ! [ -x "$(command -v cfssl)" ]; then
  echo >&2 'err: cfssl is not installed (https://github.com/cloudflare/cfssl)'
  exit 1
fi

pushd "prod" || (echo 'err: no prod directory' && exit 1)

if [ ! -f .passphrase ]; then
  LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | fold -w 32 | head -n 1 >.passphrase
fi

PASSPHRASE=$(cat .passphrase)

if [ ! -f ca.pem ]; then
  cfssl genkey -initca "ca-csr.json" | cfssljson -bare "ca"
  openssl ec -in "ca-key.pem" -out "ca-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
fi

for i in cloudserver homeserver; do
  cfssl gencert -profile www -ca ca.pem -ca-key ca-key.pem "$i-csr.json" | cfssljson -bare "$i"
  openssl ec -in "$i-key.pem" -out "$i-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
  cat "$i.pem" "$i-key.pem" >"$i-full.pem"
done

echo "passphrase: $PASSPHRASE"

popd || exit 1
