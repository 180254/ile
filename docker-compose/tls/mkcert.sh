#!/bin/bash

if ! [ -x "$(command -v cfssl)" ]; then
  >&2 echo 'err: cfssl is not installed (https://github.com/cloudflare/cfssl)'
  exit 1
fi

if [ ! -f .passphrase ]; then
  tr -dc '[:alpha:]' < /dev/urandom | fold -w 32 | head -n 1 > .passphrase
fi

PASSPHRASE=$(cat .passphrase)

if [ ! -f ca.pem ]; then
  cfssl genkey -initca "ca-csr.json" | cfssljson -bare "ca"
  openssl ec -in "ca-key.pem" -out "ca-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
fi

for i in cloudserver homeserver; do
  cfssl gencert -profile www -ca ca.pem -ca-key ca-key.pem "$i-csr.json"  | cfssljson -bare "$i"
  openssl ec -in "$i-key.pem" -out "$i-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
  cat "$i.pem" "$i-key.pem" > "$i-full.pem"
done

echo "passphrase: $PASSPHRASE"
