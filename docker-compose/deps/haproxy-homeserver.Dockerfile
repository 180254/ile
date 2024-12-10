# https://github.com/docker-library/haproxy/blob/master/3.1/Dockerfile
FROM haproxy:3.1.0-bookworm

USER root

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

USER haproxy

# IHH = ile dep HAProxy
ARG ILE_IDH_CRT_FILE="/tls/full.pem"
ARG ILE_IDH_QUESTDB_HOST="questdb"
ARG ILE_IDH_QUESTDB_RESTAPI_USERNAME="admin"
ARG ILE_IDH_QUESTDB_RESTAPI_PASSWORD="password"
ARG ILE_IDH_GRAFANA_HOST="grafana"
ARG ILE_IDH_QUESTDB_STATS_USERNAME="admin"
ARG ILE_IDH_QUESTDB_STATS_PASSWORD="password"

# http://docs.haproxy.org/2.8/configuration.html
# tcplog  - http://docs.haproxy.org/2.8/configuration.html#8.2.2
# httplog - http://docs.haproxy.org/2.8/configuration.html#8.2.3
COPY <<EOF /usr/local/etc/haproxy/haproxy.cfg
global
    maxconn 1000
    log stdout local0

    # generated 2023-09-04, Mozilla Guideline v5.7, HAProxy 2.1, OpenSSL 1.1.1k, modern configuration, no HSTS, no OCSP
    # https://ssl-config.mozilla.org/#server=haproxy&version=2.1&config=modern&openssl=1.1.1k&hsts=false&ocsp=false&guideline=5.7
    ssl-default-bind-ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256
    ssl-default-bind-options prefer-client-ciphers no-sslv3 no-tlsv10 no-tlsv11 no-tlsv12 no-tls-tickets
    ssl-default-server-ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256
    ssl-default-server-options no-sslv3 no-tlsv10 no-tlsv11 no-tlsv12 no-tls-tickets

defaults
    log global
    timeout client 5s
    timeout connect 5s
    timeout server 5s
    timeout http-request 5s

frontend health
    mode http
    bind 127.0.0.1:80
    http-request return status 200

frontend stats
    mode http
    bind *:4040 ssl crt ${ILE_IDH_CRT_FILE} alpn h2,http/1.1
    stats enable
    stats uri /
    stats auth ${ILE_IDH_QUESTDB_STATS_USERNAME}:${ILE_IDH_QUESTDB_STATS_PASSWORD}

userlist questdb_rest_api_and_web_console_userlist
   user ${ILE_IDH_QUESTDB_RESTAPI_USERNAME} insecure-password ${ILE_IDH_QUESTDB_RESTAPI_PASSWORD}

frontend questdb_rest_api_and_web_console
    mode http
    option httplog
    bind :9000 ssl crt ${ILE_IDH_CRT_FILE} alpn h2,http/1.1
    http-request auth unless { http_auth(questdb_rest_api_and_web_console_userlist) }
    use_backend questdb_rest_api_and_web_console

backend questdb_rest_api_and_web_console
    mode http
    server server1 ${ILE_IDH_QUESTDB_HOST}:9000

frontend questdb_influxdb_line_protocol
    mode tcp
    option tcplog
    bind :9009 ssl crt ${ILE_IDH_CRT_FILE}
    use_backend questdb_influxdb_line_protocol

backend questdb_influxdb_line_protocol
    mode tcp
    server server1 ${ILE_IDH_QUESTDB_HOST}:9009

frontend questdb_prostgres_wire_protocol
    mode tcp
    option tcplog
    #bind :8812 ssl crt ${ILE_IDH_CRT_FILE}
    bind :8812
    use_backend questdb_prostgres_wire_protocol

backend questdb_prostgres_wire_protocol
    mode tcp
    server server1 ${ILE_IDH_QUESTDB_HOST}:8812

frontend questdb_min_health_and_prometheus_metrics
    mode http
    option httplog
    bind :9003 ssl crt ${ILE_IDH_CRT_FILE} alpn h2,http/1.1
    use_backend questdb_min_health_and_prometheus_metrics

backend questdb_min_health_and_prometheus_metrics
    mode http
    server server1 ${ILE_IDH_QUESTDB_HOST}:9003

frontend grafana
    mode http
    option httplog
    bind :3000 ssl crt ${ILE_IDH_CRT_FILE} alpn h2,http/1.1
    use_backend grafana

backend grafana
    mode http
    server server1 ${ILE_IDH_GRAFANA_HOST}:3000
EOF
