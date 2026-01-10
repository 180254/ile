#!/usr/bin/env bash

if ! command -v cfssl &>/dev/null; then
  echo >&2 "Error: cfssl is not installed (https://github.com/cloudflare/cfssl)"
  exit 1
fi

if [[ "$#" -lt 1 ]]; then
  echo >&2 "Usage: $0 environment"
  echo >&2 "  environment: demo, prod"
  exit 1
fi

ENVIRONMENT="$1"

if ! pushd "$ENVIRONMENT" &>/dev/null; then
  echo "Error: no $ENVIRONMENT directory"
  exit 1
fi

if [ ! -f .passphrase ]; then
  LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | fold -w 32 | head -n 1 >.passphrase
fi

PASSPHRASE=$(cat .passphrase)

openssl_checkend() {
  local file="$1"
  local one_month_seconds=2592000
  local enddate

  if [ ! -f "$file" ]; then
    echo "checkend: $file does not exist"
    return 1
  fi

  enddate=$(openssl x509 -enddate -noout -in "$file")
  if openssl x509 -checkend "$one_month_seconds" -noout -in "$file" >/dev/null; then
    echo "checkend: $file is valid ($enddate)"
    return 0
  else
    echo "checkend: $file is expiring soon ($enddate)"
    return 1
  fi
}

if ! openssl_checkend "ca.pem"; then
  if [ ! -f ca-csr.json ]; then
    echo "Error: no ca-csr.json file"
    exit 1
  fi

  cfssl genkey -initca "ca-csr.json" | cfssljson -bare "ca"
  openssl ec -in "ca-key.pem" -out "ca-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
fi

for server in cloudserver homeserver laptop; do
  if ! openssl_checkend "$server.pem"; then
    if [ ! -f "$server-csr.json" ]; then
      echo "Error: no $server-csr.json file"
      exit 1
    fi

    cfssl gencert -profile www -ca ca.pem -ca-key ca-key.pem "$server-csr.json" | cfssljson -bare "$server"
    openssl ec -in "$server-key.pem" -out "$server-key-enc.pem" -aes256 -passout "pass:$PASSPHRASE"
    cat "$server.pem" "$server-key.pem" >"$server-full.pem"
  fi
done

echo "Passphrase: $PASSPHRASE"

popd || exit 1
