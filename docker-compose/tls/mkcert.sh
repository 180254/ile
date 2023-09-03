#!/bin/bash

if ! [ -x "$(command -v cfssl)" ]; then
  >&2 echo 'err: cfssl is not installed (https://github.com/cloudflare/cfssl)'
  exit 1
fi

PASSPHRASE=$(tr -dc '[:alpha:]' < /dev/urandom | fold -w 32 | head -n 1)

cfssl genkey -initca ca-csr.json | cfssljson -bare ca
cfssl gencert -profile www -ca ca.pem -ca-key ca-key.pem cloud-csr.json  | cfssljson -bare cloud

cat cloud.pem ca.pem > cloudbundle.pem

openssl ec -in cloud-key.pem -out cloud-key-enc.pem -aes256 -passout "pass:$PASSPHRASE"
openssl ec -in ca-key.pem -out ca-key-enc.pem -aes256 -passout "pass:$PASSPHRASE"

echo "passphrase: $PASSPHRASE"
