#!venv/bin/python3
import asyncio
import http.server
import itertools
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import typing
import urllib.parse
import urllib.request

import requests
import requests.auth
import websockets

from ile_shared_tools import configure_sigterm_handler, json_dumps, print_, print_debug, print_exception

"""
The script will scrape data from Shelly's devices and insert them into QuestDB.

Supported devices:
Name                | Model        | Tables in the QuestDB db                      | Scrape strategy
Shelly Plug         | SHPLG-1      | shelly_plugs_meter1                           | API polling
Shelly Plug S       | SHPLG-S      | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plug US      | SHPLG-U1     | shelly_plugs_meter1                           | API polling
Shelly Plug E       | SHPLG2-1     | shelly_plugs_meter1                           | API polling
Shelly Plus Plug IT | SNPL-00110IT | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug S  | SNPL-00112EU | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug UK | SNPL-00112UK | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug US | SNPL-00116US | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly H&T          | SHHT-1       | shelly_ht_meter1,shelly_ht_meter2             | webhook (HTTP server)
Shelly Plus H&T     | SNSN-0013A   | shelly_ht_meter1                              | receiving notifications (WebSocket)

Device configuration:
- Scape strategy: API polling
  Pass the IP address of the device using the ILE_SHELLY_IPS environment variable.
- Scape strategy: webhook (HTTP server)
  Configure your devices so that the "report sensor values" URL is "http://{machine_ip}:9080/{ile_auth_token}".
- Scape strategy: receiving notification (WebSocket)
  Configure your devices so that the outgoing WebSocket server is "ws://{machine_ip}:9081/{ile_auth_token}".

You can configure the script using environment variables.
Check the Env class below to determine what variables you can set.
"""


# --------------------- CONFIG ------------------------------------------------


class Env:
    # Set to true if shelly devices are not reachable from the machine running the script.
    ILE_CLOUD_MODE: str = os.environ.get("ILE_CLOUD_MODE", "false")

    ILE_QUESTDB_HOST: str = os.environ.get("ILE_QUESTDB_HOST", "localhost")
    ILE_QUESTDB_PORT: str = os.environ.get("ILE_QUESTDB_PORT", "9009")
    ILE_QUESTDB_SSL: str = os.environ.get("ILE_QUESTDB_SSL", "false")
    ILE_QUESTDB_SSL_CAFILE: str = os.environ.get("ILE_QUESTDB_SSL_CAFILE", "")
    ILE_QUESTDB_SSL_CHECKHOSTNAME: str = os.environ.get("ILE_QUESTDB_SSL_CHECKHOSTNAME", "true")

    # ILE_SHELLY_IPS=comma-separated list of IPs
    # List here the supported devices for which the script uses the 'API polling' strategy, and http protocol.
    ILE_SHELLY_IPS: str = os.environ.get("ILE_SHELLY_IPS", "")

    # ILE_SHELLY_SSL_IPS=comma-separated list of IPs
    # List here the supported devices for which the script uses the 'API polling' strategy, and https protocol.
    ILE_SHELLY_SSL_IPS: str = os.environ.get("ILE_SHELLY_SSL_IPS", "")

    # ILE_SHELLY_SSL_IPS_VERIFY=path to the CA file, True, False, empty (= None)
    # https://requests.readthedocs.io/en/latest/user/advanced/#ssl-cert-verification
    ILE_SHELLY_SSL_IPS_VERIFY: str = os.environ.get("ILE_SHELLY_SSL_IPS_VERIFY", "")

    # ILE_SOCKET_TIMEOUT=intValue (seconds)
    # ILE_HTTP_TIMEOUT=intValue (seconds)
    ILE_SOCKET_TIMEOUT: str = os.environ.get("ILE_SOCKET_TIMEOUT", "10")
    ILE_HTTP_TIMEOUT: str = os.environ.get("ILE_HTTP_TIMEOUT", "10")

    # ILE_SCRAPE_INTERVAL=floatValue (seconds)
    # ILE_BACKOFF_STRATEGY=comma-separated list of floats (seconds)
    ILE_SCRAPE_INTERVAL: str = os.environ.get("ILE_SCRAPE_INTERVAL", "60")
    ILE_BACKOFF_STRATEGY: str = os.environ.get("ILE_BACKOFF_STRATEGY", "0.5,1,3,3,5,60,90")

    ILE_SHELLY_GEN1_WEBHOOK_ENABLED: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_ENABLED", "true")
    ILE_SHELLY_GEN1_WEBHOOK_BIND_HOST: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_BIND_HOST", "0.0.0.0")
    ILE_SHELLY_GEN1_WEBHOOK_BIND_PORT: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_BIND_PORT", "9080")
    ILE_SHELLY_GEN1_WEBHOOK_SSL: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_SSL", "false")
    ILE_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE", "server.crt")
    ILE_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE", "server.key")
    ILE_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD: str = os.environ.get("ILE_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD", "")

    ILE_SHELLY_GEN2_WEBSOCKET_ENABLED: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_ENABLED", "true")
    ILE_SHELLY_GEN2_WEBSOCKET_BIND_HOST: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_BIND_HOST", "0.0.0.0")
    ILE_SHELLY_GEN2_WEBSOCKET_BIND_PORT: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_BIND_PORT", "9081")
    ILE_SHELLY_GEN2_WEBSOCKET_SSL: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_SSL", "false")
    ILE_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE", "server.crt")
    ILE_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE", "server.key")
    ILE_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD: str = os.environ.get("ILE_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD", "")

    # https://shelly-api-docs.shelly.cloud/gen1/#http-dialect
    ILE_SHELLY_GEN1_AUTH_USERNAME = os.environ.get("ILE_SHELLY_GEN1_AUTH_USERNAME", "")
    ILE_SHELLY_GEN1_AUTH_PASSWORD = os.environ.get("ILE_SHELLY_GEN1_AUTH_PASSWORD", "")

    # https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/
    ILE_SHELLY_GEN2_AUTH_USERNAME = os.environ.get("ILE_SHELLY_GEN2_AUTH_USERNAME", "")
    ILE_SHELLY_GEN2_AUTH_PASSWORD = os.environ.get("ILE_SHELLY_GEN2_AUTH_PASSWORD", "")

    ILE_AUTH_TOKEN: str = os.environ.get("ILE_AUTH_TOKEN", "")


class Config:
    cloud_mode: bool = Env.ILE_CLOUD_MODE.lower() == "true"

    questdb_address: tuple[str, int] = (Env.ILE_QUESTDB_HOST, int(Env.ILE_QUESTDB_PORT))
    questdb_ssl: bool = Env.ILE_QUESTDB_SSL.lower() == "true"
    questdb_ssl_cafile: str | None = Env.ILE_QUESTDB_SSL_CAFILE or None
    questdb_ssl_checkhostname: bool = Env.ILE_QUESTDB_SSL_CHECKHOSTNAME.lower() == "true"

    shelly_devices_ips: typing.Sequence[str] = list(filter(None, Env.ILE_SHELLY_IPS.split(",")))

    shelly_devices_ssl_ips: typing.Sequence[str] = list(filter(None, Env.ILE_SHELLY_SSL_IPS.split(",")))
    shelly_devices_ssl_ips_verify: str | bool | None
    match Env.ILE_SHELLY_SSL_IPS_VERIFY.lower():
        case "true":
            shelly_devices_ssl_ips_verify = True
        case "false":
            shelly_devices_ssl_ips_verify = False
        case "":
            shelly_devices_ssl_ips_verify = None
        case _:
            shelly_devices_ssl_ips_verify = Env.ILE_SHELLY_SSL_IPS_VERIFY

    socket_timeout_seconds: int = int(Env.ILE_SOCKET_TIMEOUT)
    http_timeout_seconds: int = int(Env.ILE_HTTP_TIMEOUT)

    scrape_interval_seconds: int = int(Env.ILE_SCRAPE_INTERVAL)
    backoff_strategy_seconds: typing.Sequence[float] = list(
        map(float, filter(None, Env.ILE_BACKOFF_STRATEGY.split(","))),
    )

    shelly_gen1_webhook_enabled: bool = Env.ILE_SHELLY_GEN1_WEBHOOK_ENABLED.lower() == "true"
    shelly_gen1_webhook_bind_address: tuple[str, int] = (
        Env.ILE_SHELLY_GEN1_WEBHOOK_BIND_HOST,
        int(Env.ILE_SHELLY_GEN1_WEBHOOK_BIND_PORT),
    )
    shelly_gen1_webhook_ssl: bool = Env.ILE_SHELLY_GEN1_WEBHOOK_SSL.lower() == "true"
    shelly_gen1_webhook_ssl_certfile: str = Env.ILE_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE
    shelly_gen1_webhook_ssl_keyfile: str = Env.ILE_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE
    shelly_gen1_webhook_ssl_password: str | None = Env.ILE_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD or None

    shelly_gen2_websocket_enabled: bool = Env.ILE_SHELLY_GEN2_WEBSOCKET_ENABLED.lower() == "true"
    shelly_gen2_websocket_bind_address: tuple[str, int] = (
        Env.ILE_SHELLY_GEN2_WEBSOCKET_BIND_HOST,
        int(Env.ILE_SHELLY_GEN2_WEBSOCKET_BIND_PORT),
    )
    shelly_gen2_websocket_ssl: bool = Env.ILE_SHELLY_GEN2_WEBSOCKET_SSL.lower() == "true"
    shelly_gen2_websocket_ssl_certfile: str = Env.ILE_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE
    shelly_gen2_websocket_ssl_keyfile: str = Env.ILE_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE
    shelly_gen2_websocket_ssl_password: str | None = Env.ILE_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD or None

    shelly_gen1_auth: requests.auth.AuthBase | None = (
        requests.auth.HTTPBasicAuth(Env.ILE_SHELLY_GEN1_AUTH_USERNAME, Env.ILE_SHELLY_GEN1_AUTH_PASSWORD)
        if Env.ILE_SHELLY_GEN1_AUTH_USERNAME and Env.ILE_SHELLY_GEN1_AUTH_PASSWORD
        else None
    )

    shelly_gen2_auth: requests.auth.AuthBase | None = (
        requests.auth.HTTPDigestAuth(Env.ILE_SHELLY_GEN2_AUTH_USERNAME, Env.ILE_SHELLY_GEN2_AUTH_PASSWORD)
        if Env.ILE_SHELLY_GEN2_AUTH_USERNAME and Env.ILE_SHELLY_GEN2_AUTH_PASSWORD
        else None
    )

    auth_token: str = Env.ILE_AUTH_TOKEN


# --------------------- HELPERS -----------------------------------------------


def time_is_synced(unix_timestamp_seconds: float) -> bool:
    one_day_in_seconds = 24 * 60 * 60
    return unix_timestamp_seconds > one_day_in_seconds


def http_call(device_ip: str, path_and_query: str, auth: requests.auth.AuthBase | None = None) -> dict:
    ssl_ = device_ip in Config.shelly_devices_ssl_ips
    proto = "https" if ssl_ else "http"
    verify = Config.shelly_devices_ssl_ips_verify if ssl_ else None

    response = requests.get(
        f"{proto}://{device_ip}/{path_and_query}",
        timeout=Config.http_timeout_seconds,
        auth=auth,
        verify=verify,
    )
    response.raise_for_status()
    data = response.json()
    print_debug(lambda: json_dumps(data))
    return data


# https://shelly-api-docs.shelly.cloud/gen2/General/RPCProtocol
def http_rpc_call(
    device_ip: str, method: str, params: dict | None = None, auth: requests.auth.AuthBase | None = None
) -> dict:
    ssl_ = device_ip in Config.shelly_devices_ssl_ips
    proto = "https" if ssl_ else "http"
    verify = Config.shelly_devices_ssl_ips_verify if ssl_ else None

    # https://shelly-api-docs.shelly.cloud/gen2/General/RPCProtocol#request-frame
    post_body = {"jsonrpc": "2.0", "id": 1, "src": "ile", "method": method, "params": params}
    if params is None:
        del post_body["params"]

    response = requests.post(
        f"{proto}://{device_ip}/rpc",
        json=post_body,
        timeout=Config.http_timeout_seconds,
        auth=auth,
        verify=verify,
    )
    response.raise_for_status()
    data = response.json()
    print_debug(lambda: json_dumps(data))
    return data


# --------------------- QUESTDB -----------------------------------------------


# ilp = InfluxDB line protocol
# https://questdb.io/docs/reference/api/ilp/overview/
def write_ilp_to_questdb(data: str) -> None:
    if data is None or data == "":
        return

    # Fix ilp data.
    # Remove name=value pairs where value is None.
    if "None" in data:
        data = re.sub(r"[a-zA-Z0-9_]+=None,?", "", data).replace(" ,", " ").replace(", ", " ")

    print_(data, end="")

    if Config.questdb_ssl:
        ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=Config.questdb_ssl_cafile)
        ssl_context.check_hostname = Config.questdb_ssl_checkhostname
    else:
        ssl_context = None

    # https://github.com/questdb/questdb.io/commit/35ca3c326ab0b3448ef9fdb39eb60f1bd45f8506
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock0,
        ssl_context.wrap_socket(sock0, server_hostname=Config.questdb_address[0]) if ssl_context else sock0 as sock,
    ):
        sock.settimeout(Config.socket_timeout_seconds)
        sock.connect(Config.questdb_address)
        sock.sendall(data.encode())

        # Send one more empty line after a while.
        # Make sure that the server did not close the connection
        # (questdb will do that asynchronously if the data was incorrect).
        # https://github.com/questdb/questdb/blob/7.2.1/core/src/main/java/io/questdb/network/AbstractIODispatcher.java#L149
        time.sleep(0.050)
        sock.sendall(b"\n")

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()


# --------------------- SHELLY Gen1&Gen2 --------------------------------------


def shelly_get_device_gen_and_type(device_ip: str) -> tuple[int, str]:
    # https://shelly-api-docs.shelly.cloud/gen1/#shelly
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly/#http-endpoint-shelly
    shelly = http_call(device_ip, "shelly")

    # gen2
    if "gen" in shelly:
        device_gen = shelly["gen"]
        device_type = shelly["model"] if device_gen == 2 else "_unknown_"

    # gen1
    else:
        device_gen = 1
        device_type = shelly["type"]

    return device_gen, device_type


# --------------------- SHELLY Gen1 -------------------------------------------


def shelly_get_gen1_device_info(device_ip: str) -> tuple[str, str, str]:
    # https://shelly-api-docs.shelly.cloud/gen1/#settings
    settings = http_call(device_ip, "settings", auth=Config.shelly_gen1_auth)

    device_type = settings["device"]["type"]
    device_id = settings["device"]["hostname"]
    device_name = settings["name"]

    return device_type, device_id, device_name


def shelly_get_gen1_device_status_ilp(device_ip: str, device_type: str, device_id: str, device_name: str) -> str:
    # https://shelly-api-docs.shelly.cloud/gen1/#shelly-plug-plugs-coiot
    if device_type in ("SHPLG-1", "SHPLG-S", "SHPLG-U1", "HPLG2-1"):
        # https://shelly-api-docs.shelly.cloud/gen1/#status
        # https://shelly-api-docs.shelly.cloud/gen1/#shelly-plug-plugs-status
        status = http_call(device_ip, "status", auth=Config.shelly_gen1_auth)
        return shelly_gen1_plug_status_to_ilp(device_id, device_name, status)

    # https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-coiot
    if device_type == "SHHT-1":
        # https://shelly-api-docs.shelly.cloud/gen1/#status
        # https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-status
        status = http_call(device_ip, "status", auth=Config.shelly_gen1_auth)
        return shelly_gen1_ht_status_to_ilp(device_id, device_name, status)

    print_(
        f"The shelly_get_gen1_device_status_ilp failed for device_ip={device_ip} "
        f"due to unsupported device_type={device_type}.",
        file=sys.stderr,
    )
    return ""


def shelly_gen1_plug_status_to_ilp(device_id: str, device_name: str, status: dict) -> str:
    # status = https://shelly-api-docs.shelly.cloud/gen1/#shelly-plug-plugs-status

    timestamp = status["unixtime"]
    if not time_is_synced(timestamp):
        timestamp = int(time.time())
    nano = "000000000"

    # InfluxDB line protocol data
    data = ""

    idx = 0
    meter = status["meters"][idx]
    relay = status["relays"][idx]

    data += (
        f"shelly_plugs_meter1,device_id={device_id},device_name={device_name},idx={idx} "
        f"is_on={relay['ison']},"
        f"power={meter['power']},"
        f"overpower={meter['overpower']},"
        f"is_overpower={relay['overpower']},"
        f"is_valid={meter['is_valid']},"
        f"counters_0={meter['counters'][0]},"
        f"counters_1={meter['counters'][1]},"
        f"counters_2={meter['counters'][2]},"
        f"total={meter['total']} "
        f"{timestamp}{nano}\n"
    )

    # PlugS only
    if status.get("temperature", None) is not None:
        data += (
            f"shelly_plugs_temperature1,device_id={device_id},device_name={device_name} "
            f"overtemperature={status['overtemperature']},"
            f"tmp_tc={status['tmp']['tC']},"
            f"tmp_is_valid={status['tmp']['is_valid']} "
            f"{timestamp}{nano}\n"
        )

    return data


def shelly_gen1_ht_status_to_ilp(device_id: str, device_name: str, status: dict) -> str:
    # status = https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-status

    timestamp = status["unixtime"]
    if not time_is_synced(timestamp):
        timestamp = int(time.time())
    nano = "000000000"

    # InfluxDB line protocol data
    return (
        f"shelly_ht_meter1,device_id={device_id},device_name={device_name} "
        f"is_valid={status['is_valid']},"
        f"tmp_tc={status['tmp']['tC']},"
        f"tmp_is_valid={status['tmp']['is_valid']},"
        f"hum_value={status['hum']['value']},"
        f"hum_is_valid={status['hum']['is_valid']},"
        f"bat_value={status['bat']['value']},"
        f"bat_voltage={status['bat']['voltage']},"
        f"connect_retries={status['connect_retries']},"
        f"sensor_error={status.get('sensor_error', 0)} "
        f"{timestamp}{nano}\n"
    )


def shelly_gen1_ht_report_to_ilp(device_id: str, temp: str, hum: str) -> str:
    # https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-settings-actions

    timestamp = int(time.time())
    nano = "000000000"

    # InfluxDB line protocol data
    return f"shelly_ht_meter2,device_id={device_id} temp={temp},hum={hum} {timestamp}{nano}\n"


# Handler for Shelly H&T's action "report sensor values".
# https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-settings-actions
class ShellyGen1HtReportSensorValuesHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            self.send_response(200)
            self.end_headers()

            device_ip = self.client_address[0]

            if Config.auth_token and Config.auth_token not in self.path:
                print_(
                    f"The ShellyGen1HtReportSensorValuesHandler failed for device_ip={device_ip} "
                    f"due to unsupported path: '{self.path}'.",
                    file=sys.stderr,
                )
                return

            url_components = urllib.parse.urlparse(self.path)
            query_string = urllib.parse.parse_qs(url_components.query)
            is_valid_ht_report = "id" in query_string and "temp" in query_string and "hum" in query_string
            print_debug(lambda: self.path)

            if is_valid_ht_report:
                device_id = query_string["id"][0]
                temp = query_string["temp"][0]
                hum = query_string["hum"][0]

                data = shelly_gen1_ht_report_to_ilp(device_id, temp, hum)

                try:
                    if not Config.cloud_mode:
                        # The http connection is still in progress. Device has active Wi-Fi.
                        device_type, device_id, device_name = shelly_get_gen1_device_info(device_ip)
                        data += shelly_get_gen1_device_status_ilp(device_ip, device_type, device_id, device_name)

                except Exception as exception:
                    print_exception(exception)

                # I/O operation that may be happening after the connection is closed.
                questdb_thread = threading.Thread(target=write_ilp_to_questdb, args=(data,), daemon=False)
                questdb_thread.start()

            else:
                print_(
                    f"The ShellyGen1HtReportSensorValuesHandler failed for device_ip={device_ip} "
                    f"due to unsupported query: '{self.path}'.",
                    file=sys.stderr,
                )

        except Exception as exception:
            print_exception(exception)


# --------------------- SHELLY Gen2 -------------------------------------------


def shelly_get_gen2_device_name(device_ip: str) -> str:
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys#sysgetconfig
    sysconfig = http_call(device_ip, "rpc/Sys.GetConfig", auth=Config.shelly_gen2_auth)
    return sysconfig["device"]["name"]


def shelly_get_gen2_device_status_ilp(device_ip: str, device_type: str, device_name: str) -> str:
    # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-it
    # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-s
    # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-uk
    # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-us
    if device_type in ("SNPL-00110IT", "SNPL-00112EU", "SNPL-00112UK", "SNPL-00116US"):
        # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch#status
        status = http_rpc_call(device_ip, "Switch.GetStatus", {"id": 0}, auth=Config.shelly_gen2_auth)
        return shelly_gen2_plug_status_to_ilp(device_name, status)

    print_(
        f"The shelly_get_gen2_device_status_ilp failed for device_ip={device_ip} "
        f"due to unsupported device_type={device_type}.",
        file=sys.stderr,
    )
    return ""


def shelly_gen2_plug_status_to_ilp(device_name: str, status: dict) -> str:
    # status = Switch.GetStatus result
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch#status

    timestamp = int(time.time())
    nano = "000000000"

    # InfluxDB line protocol data
    data = ""

    device_id = status["src"]
    idx = 0

    result = status["result"]
    has_errors = "errors" in result

    is_on = result["output"]
    power = result["apower"]
    _overpower = None  # Value available in the Switch.GetConfig.
    is_overpower = has_errors and "overpower" in result["errors"]
    is_valid = power is not None
    counters_0 = result["aenergy"]["by_minute"][0] * 0.06  # Milliwatt-hours to Watt-minutes
    counters_1 = result["aenergy"]["by_minute"][1] * 0.06  # Milliwatt-hours to Watt-minutes
    counters_2 = result["aenergy"]["by_minute"][2] * 0.06  # Milliwatt-hours to Watt-minutes
    total = result["aenergy"]["total"] * 60  # Watt-hours to Watt-minutes
    voltage = result["voltage"]
    is_overvoltage = has_errors and "overvoltage" in result["errors"]
    is_undervoltage = has_errors and "undervoltage" in result["errors"]
    current = result["current"]
    is_overcurrent = has_errors and "overcurrent" in result["errors"]

    data += (
        f"shelly_plugs_meter1,device_id={device_id},device_name={device_name},idx={idx} "
        f"is_on={is_on},"
        f"power={power},"
        f"is_overpower={is_overpower},"
        f"is_valid={is_valid},"
        f"counters_0={counters_0},"
        f"counters_1={counters_1},"
        f"counters_2={counters_2},"
        f"total={total},"
        f"voltage={voltage},"
        f"is_overvoltage={is_overvoltage},"
        f"is_undervoltage={is_undervoltage},"
        f"current={current},"
        f"is_overcurrent={is_overcurrent} "
        f"{timestamp}{nano}\n"
    )

    overtemperature = has_errors and "overtemp" in result["errors"]
    tmp_tc = result["temperature"]["tC"]
    tmp_is_valid = tmp_tc is not None

    data += (
        f"shelly_plugs_temperature1,device_id={device_id},device_name={device_name} "
        f"overtemperature={overtemperature},"
        f"tmp_tc={tmp_tc},"
        f"tmp_is_valid={tmp_is_valid} "
        f"{timestamp}{nano}\n"
    )

    return data


def shelly_gen2_plusht_status_to_ilp(device_name: str | None, status: dict) -> str:
    # status = status in "NotifyFullStatus" notification format
    # https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/#notifyfullstatus

    # Required components: sys, devicepower:0, temperature:0, humidity:0
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/DevicePower/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Temperature/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Humidity/

    timestamp = int(status["params"]["ts"] or 0)
    if not time_is_synced(timestamp):
        timestamp = int(time.time())
    nano = "000000000"

    device_id = status["src"]
    params = status["params"]

    if device_name is None:
        device_name = device_id

    tmp_tc = params["temperature:0"]["tC"]
    tmp_is_valid = tmp_tc is not None and "errors" not in params["temperature:0"]
    hum_value = params["humidity:0"]["rh"]
    hum_is_valid = hum_value is not None and "errors" not in params["humidity:0"]
    is_valid = tmp_is_valid or hum_is_valid

    bat_value = params["devicepower:0"]["battery"]["percent"]
    bat_voltage = params["devicepower:0"]["battery"]["V"]

    # InfluxDB line protocol data
    return (
        f"shelly_ht_meter1,device_id={device_id},device_name={device_name} "
        f"is_valid={is_valid},"
        f"tmp_tc={tmp_tc},"
        f"tmp_is_valid={tmp_is_valid},"
        f"hum_value={hum_value},"
        f"hum_is_valid={hum_is_valid},"
        f"bat_value={bat_value},"
        f"bat_voltage={bat_voltage},"
        f"connect_retries=0,"
        f"sensor_error=0 "
        f"{timestamp}{nano}\n"
    )


async def shelly_gen2_outbound_websocket_handler(websocket: websockets.WebSocketServerProtocol, path: str) -> None:
    try:
        device_ip = websocket.remote_address[0]

        if Config.auth_token and Config.auth_token not in path:
            print_(
                f"The shelly_gen2_outbound_websocket_handler failed for device_ip={device_ip} "
                f"due to unsupported path '{path}'.",
                file=sys.stderr,
            )
            return

        recv = await websocket.recv()
        payload = json.loads(recv)
        print_debug(lambda: json_dumps(payload))

        src = payload["src"]

        if src.startswith("shellyplusht-"):
            # "NotifyFullStatus" messages are valuable.
            # https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/#notifyfullstatus
            # https://shelly-api-docs.shelly.cloud/gen2/General/SleepManagementForBatteryDevices
            if payload["method"] == "NotifyFullStatus":
                try:
                    # The websocket connection is still in progress. Device has active Wi-Fi.
                    device_name = shelly_get_gen2_device_name(device_ip) if not Config.cloud_mode else None

                except Exception as exception:
                    print_exception(exception)
                    return

                data = shelly_gen2_plusht_status_to_ilp(device_name, payload)

                # I/O operation that may be happening after the connection is closed.
                questdb_thread = threading.Thread(target=write_ilp_to_questdb, args=(data,), daemon=False)
                questdb_thread.start()

        else:
            print_(
                f"The shelly_gen2_outbound_websocket_handler failed for device_ip={device_ip} "
                f"due to unsupported src={src}.",
                file=sys.stderr,
            )

    except Exception as exception:
        print_exception(exception)

    finally:
        await websocket.close()


# --------------------- Main --------------------------------------------------


def shelly_device_status_loop(sigterm_threading_event: threading.Event, device_ip: str) -> None:
    backoff_idx = -1

    while True:
        try:
            device_gen, device_type = shelly_get_device_gen_and_type(device_ip)

            while True:
                if device_gen == 1:
                    device_type, device_id, device_name = shelly_get_gen1_device_info(device_ip)
                    data = shelly_get_gen1_device_status_ilp(device_ip, device_type, device_id, device_name)

                elif device_gen == 2:
                    device_name = shelly_get_gen2_device_name(device_ip)
                    data = shelly_get_gen2_device_status_ilp(device_ip, device_type, device_name)

                else:
                    data = ""
                    print_(
                        f"The shelly_device_status_loop failed for device_ip={device_ip} "
                        f"due to unsupported device_gen={device_gen}.",
                        file=sys.stderr,
                    )

                write_ilp_to_questdb(data)

                if sigterm_threading_event.wait(Config.scrape_interval_seconds):
                    break

                backoff_idx = -1

            if sigterm_threading_event.is_set():
                break

        except Exception as exception:
            print_exception(exception)
            backoff_idx = max(0, min(backoff_idx + 1, len(Config.backoff_strategy_seconds) - 1))
            backoff = Config.backoff_strategy_seconds[backoff_idx]
            if sigterm_threading_event.wait(backoff):
                break


def main() -> int:
    print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = configure_sigterm_handler()

    for device_ip in itertools.chain(Config.shelly_devices_ips, Config.shelly_devices_ssl_ips):
        status_thread = threading.Thread(
            target=shelly_device_status_loop,
            args=(sigterm_threading_event, device_ip),
            daemon=False,
        )
        status_thread.start()

    # Handle Shelly H&T's action: "report sensor values".
    if Config.shelly_gen1_webhook_enabled:
        shelly_ht_report_webhook = http.server.ThreadingHTTPServer(
            Config.shelly_gen1_webhook_bind_address, ShellyGen1HtReportSensorValuesHandler
        )
        if Config.shelly_gen1_webhook_ssl:
            webhook_ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            webhook_ssl_context.load_cert_chain(
                Config.shelly_gen1_webhook_ssl_certfile,
                Config.shelly_gen1_webhook_ssl_keyfile,
                Config.shelly_gen1_webhook_ssl_password,
            )
            shelly_ht_report_webhook.socket = webhook_ssl_context.wrap_socket(
                shelly_ht_report_webhook.socket, server_side=True
            )
        webhook_server_thread = threading.Thread(target=shelly_ht_report_webhook.serve_forever, daemon=True)
        webhook_server_thread.start()

    if Config.shelly_gen2_websocket_enabled:
        # Act as WebSocket server. Handle gen2 notifications.
        # Let's mix classic http.server.HTTPServer with asyncio-based websockets!
        async def shelly_gen2_outbound_websocket_server() -> None:
            if Config.shelly_gen2_websocket_ssl:
                websocket_ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                websocket_ssl_context.load_cert_chain(
                    Config.shelly_gen2_websocket_ssl_certfile,
                    Config.shelly_gen2_websocket_ssl_keyfile,
                    Config.shelly_gen2_websocket_ssl_password,
                )
            else:
                websocket_ssl_context = None

            ws_server = await websockets.serve(
                shelly_gen2_outbound_websocket_handler,
                Config.shelly_gen2_websocket_bind_address[0],
                Config.shelly_gen2_websocket_bind_address[1],
                ssl=websocket_ssl_context,
            )
            await ws_server.server.serve_forever()

        # Horrible. Works and is compatible with sigterm_threading_event.
        websocket_sever_thread = threading.Thread(
            target=lambda: asyncio.run(shelly_gen2_outbound_websocket_server()),
            daemon=True,
        )
        websocket_sever_thread.start()

    print_("STARTED", file=sys.stderr)
    sigterm_threading_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
