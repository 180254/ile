#!.venv/bin/python3
from __future__ import annotations

import asyncio
import http.server
import itertools
import json
import os
import ssl
import sys
import threading
import time
import typing
import urllib.parse
import urllib.request

import aiohttp
import requests
import requests.auth
import websockets

import ile_shared_tools

# --------------------- CONFIG ------------------------------------------------

getenv = os.environ.get


# ISS = ile shelly scraper
class Env:
    # Set to true if shelly devices are not reachable from the machine running the script.
    ILE_ISS_CLOUD_MODE: str = getenv("ILE_ISS_CLOUD_MODE", "false")

    # ILE_ISS_SHELLY_IPS=comma-separated list of IPs
    # List here the supported devices for which the script uses the 'API polling' strategy, and http protocol.
    ILE_ISS_SHELLY_IPS: str = getenv("ILE_ISS_SHELLY_IPS", "")

    # ILE_ISS_SHELLY_SSL_IPS=comma-separated list of IPs
    # List here the supported devices for which the script uses the 'API polling' strategy, and https protocol.
    ILE_ISS_SHELLY_SSL_IPS: str = getenv("ILE_ISS_SHELLY_SSL_IPS", "")

    # ILE_ISS_SHELLY_SSL_IPS_VERIFY=path to the CA file, True, False, empty (= None)
    # https://requests.readthedocs.io/en/latest/user/advanced/#ssl-cert-verification
    ILE_ISS_SHELLY_SSL_IPS_VERIFY: str = getenv("ILE_ISS_SHELLY_SSL_IPS_VERIFY", "")

    # ILE_ISS_SOCKET_TIMEOUT=intValue (seconds)
    # ILE_ISS_HTTP_TIMEOUT=intValue (seconds)
    ILE_ISS_SOCKET_TIMEOUT: str = getenv("ILE_ISS_SOCKET_TIMEOUT", "10")
    ILE_ISS_HTTP_TIMEOUT: str = getenv("ILE_ISS_HTTP_TIMEOUT", "10")

    # ILE_ISS_SCRAPE_INTERVAL=floatValue (seconds)
    # ILE_ISS_BACKOFF_STRATEGY=comma-separated list of floats (seconds)
    ILE_ISS_SCRAPE_INTERVAL: str = getenv("ILE_ISS_SCRAPE_INTERVAL", "60")
    ILE_ISS_BACKOFF_STRATEGY: str = getenv("ILE_ISS_BACKOFF_STRATEGY", "0.5,1,3,3,5,60,90")

    ILE_ISS_SHELLY_GEN1_WEBHOOK_ENABLED: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_ENABLED", "true")
    ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_HOST: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_HOST", "0.0.0.0")  # noqa: S104
    ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_PORT: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_PORT", "9080")
    ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL", "false")
    ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE", "server.crt")
    ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE", "server.key")
    ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD: str = getenv("ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD", "")

    ILE_ISS_SHELLY_GEN2_WEBSOCKET_ENABLED: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_ENABLED", "true")
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_HOST: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_HOST", "0.0.0.0")  # noqa: S104
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_PORT: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_PORT", "9081")
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL", "false")
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE", "server.crt")
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE", "server.key")
    ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD: str = getenv("ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD", "")

    # https://shelly-api-docs.shelly.cloud/gen1/#http-dialect
    ILE_ISS_SHELLY_GEN1_AUTH_USERNAME = getenv("ILE_ISS_SHELLY_GEN1_AUTH_USERNAME", "")
    ILE_ISS_SHELLY_GEN1_AUTH_PASSWORD = getenv("ILE_ISS_SHELLY_GEN1_AUTH_PASSWORD", "")

    # https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/
    ILE_ISS_SHELLY_GEN2_AUTH_USERNAME = getenv("ILE_ISS_SHELLY_GEN2_AUTH_USERNAME", "")
    ILE_ISS_SHELLY_GEN2_AUTH_PASSWORD = getenv("ILE_ISS_SHELLY_GEN2_AUTH_PASSWORD", "")

    ILE_ISS_AUTH_TOKEN: str = getenv("ILE_ISS_AUTH_TOKEN", "")


class Config:
    cloud_mode: bool = Env.ILE_ISS_CLOUD_MODE.lower() == "true"

    shelly_devices_ips: typing.Sequence[str] = list(filter(None, Env.ILE_ISS_SHELLY_IPS.split(",")))

    shelly_devices_ssl_ips: typing.Sequence[str] = list(filter(None, Env.ILE_ISS_SHELLY_SSL_IPS.split(",")))
    shelly_devices_ssl_ips_verify: str | bool | None
    match Env.ILE_ISS_SHELLY_SSL_IPS_VERIFY.lower():
        case "true":
            shelly_devices_ssl_ips_verify = True
        case "false":
            shelly_devices_ssl_ips_verify = False
        case "":
            shelly_devices_ssl_ips_verify = None
        case _:
            shelly_devices_ssl_ips_verify = Env.ILE_ISS_SHELLY_SSL_IPS_VERIFY

    http_timeout_seconds: int = int(Env.ILE_ISS_HTTP_TIMEOUT)

    scrape_interval_seconds: int = int(Env.ILE_ISS_SCRAPE_INTERVAL)
    backoff_strategy_seconds: typing.Sequence[float] = list(
        map(float, filter(None, Env.ILE_ISS_BACKOFF_STRATEGY.split(",")))
    )

    shelly_gen1_webhook_enabled: bool = Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_ENABLED.lower() == "true"
    shelly_gen1_webhook_bind_address: tuple[str, int] = (
        Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_HOST,
        int(Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_BIND_PORT),
    )
    shelly_gen1_webhook_ssl: bool = Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL.lower() == "true"
    shelly_gen1_webhook_ssl_certfile: str = Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_CERTFILE
    shelly_gen1_webhook_ssl_keyfile: str = Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_KEYFILE
    shelly_gen1_webhook_ssl_password: str | None = Env.ILE_ISS_SHELLY_GEN1_WEBHOOK_SSL_PASSWORD or None

    shelly_gen2_websocket_enabled: bool = Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_ENABLED.lower() == "true"
    shelly_gen2_websocket_bind_address: tuple[str, int] = (
        Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_HOST,
        int(Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_BIND_PORT),
    )
    shelly_gen2_websocket_ssl: bool = Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL.lower() == "true"
    shelly_gen2_websocket_ssl_certfile: str = Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_CERTFILE
    shelly_gen2_websocket_ssl_keyfile: str = Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_KEYFILE
    shelly_gen2_websocket_ssl_password: str | None = Env.ILE_ISS_SHELLY_GEN2_WEBSOCKET_SSL_PASSWORD or None

    shelly_gen1_auth: requests.auth.AuthBase | None = (
        requests.auth.HTTPBasicAuth(Env.ILE_ISS_SHELLY_GEN1_AUTH_USERNAME, Env.ILE_ISS_SHELLY_GEN1_AUTH_PASSWORD)
        if Env.ILE_ISS_SHELLY_GEN1_AUTH_USERNAME and Env.ILE_ISS_SHELLY_GEN1_AUTH_PASSWORD
        else None
    )

    shelly_gen2_auth: requests.auth.AuthBase | None = (
        requests.auth.HTTPDigestAuth(Env.ILE_ISS_SHELLY_GEN2_AUTH_USERNAME, Env.ILE_ISS_SHELLY_GEN2_AUTH_PASSWORD)
        if Env.ILE_ISS_SHELLY_GEN2_AUTH_USERNAME and Env.ILE_ISS_SHELLY_GEN2_AUTH_PASSWORD
        else None
    )

    auth_token: str = Env.ILE_ISS_AUTH_TOKEN


# --------------------- HELPERS -----------------------------------------------


def time_is_synced(first_timestamp: float, second_timestamp: float) -> bool:
    one_minute = 60
    return first_timestamp > 0 and second_timestamp > 0 and abs(first_timestamp - second_timestamp) < one_minute


def http_call(
    device_ip: str,
    path_and_query: str,
    auth: requests.auth.AuthBase | None = None,
) -> dict[str, typing.Any]:
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
    data = typing.cast("dict[str, typing.Any]", response.json())
    ile_shared_tools.print_debug(lambda: ile_shared_tools.json_dumps(data))
    return data


# https://shelly-api-docs.shelly.cloud/gen2/General/RPCProtocol
def http_rpc_call(
    device_ip: str,
    method: str,
    params: dict[str, typing.Any] | None = None,
    auth: requests.auth.AuthBase | None = None,
) -> dict[str, typing.Any]:
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
    data = typing.cast("dict[str, typing.Any]", response.json())
    ile_shared_tools.print_debug(lambda: ile_shared_tools.json_dumps(data))
    return data


async def http_call_async(
    device_ip: str,
    path_and_query: str,
    auth: requests.auth.AuthBase | None = None,
) -> dict[str, typing.Any]:
    ssl_ = device_ip in Config.shelly_devices_ssl_ips
    proto = "https" if ssl_ else "http"
    verify = Config.shelly_devices_ssl_ips_verify if ssl_ else None

    timeout = aiohttp.ClientTimeout(total=Config.http_timeout_seconds)

    if verify and isinstance(verify, str):  # verify=CA file path
        ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=verify)
        connector = aiohttp.TCPConnector(ssl=ssl_context)
    elif verify is False:  # verify=False
        connector = aiohttp.TCPConnector(ssl=False)
    else:  # verify=True/verify=None
        connector = aiohttp.TCPConnector()

    auth2 = None
    if auth and isinstance(auth, requests.auth.HTTPBasicAuth):
        auth2 = aiohttp.BasicAuth(str(auth.username), str(auth.password))

    async with (
        aiohttp.ClientSession(timeout=timeout, connector=connector, auth=auth2) as session,
        session.get(f"{proto}://{device_ip}/{path_and_query}") as response,
    ):
        response.raise_for_status()
        data = typing.cast("dict[str, typing.Any]", await response.json())
        ile_shared_tools.print_debug(lambda: ile_shared_tools.json_dumps(data))
        return data


# --------------------- SHELLY Gen1&Gen2 --------------------------------------


def shelly_get_device_gen_and_type(device_ip: str) -> tuple[int, str]:
    # https://shelly-api-docs.shelly.cloud/gen1/#shelly
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly/#http-endpoint-shelly
    shelly = http_call(device_ip, "shelly")

    # gen2+
    if "gen" in shelly:
        device_gen = shelly["gen"]
        device_type = shelly["model"]

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
    # https://shelly-api-docs.shelly.cloud/gen1/#shelly-plug-plugs
    if device_type in (
        # Shelly Plug
        # https://kb.shelly.cloud/knowledge-base/shelly-plug
        "SHPLG-1",
        # Shelly Plug S
        # https://kb.shelly.cloud/knowledge-base/shelly-plug-s
        "SHPLG-S",
        # Shelly Plug US
        # https://kb.shelly.cloud/knowledge-base/shelly-plug-us
        "SHPLG-U1",
        # Shelly Plug E
        "SHPLG2-1",
    ):
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

    ile_shared_tools.print_(
        f"The shelly_get_gen1_device_status_ilp failed for device_ip={device_ip} "
        f"due to unsupported device_type={device_type}.",
        file=sys.stderr,
    )
    return ""


def shelly_gen1_plug_status_to_ilp(device_id: str, device_name: str, status: dict[str, typing.Any]) -> str:
    # status = https://shelly-api-docs.shelly.cloud/gen1/#shelly-plug-plugs-status

    status_timestamp = status["unixtime"]
    os_timestamp = int(time.time())

    if time_is_synced(status_timestamp, os_timestamp):
        timestamp = status_timestamp
    else:
        timestamp = os_timestamp

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
    if status.get("temperature") is not None:
        data += (
            f"shelly_plugs_temperature1,device_id={device_id},device_name={device_name} "
            f"overtemperature={status['overtemperature']},"
            f"tmp_tc={status['tmp']['tC']},"
            f"tmp_is_valid={status['tmp']['is_valid']} "
            f"{timestamp}{nano}\n"
        )

    return data


def shelly_gen1_ht_status_to_ilp(device_id: str, device_name: str, status: dict[str, typing.Any]) -> str:
    # status = https://shelly-api-docs.shelly.cloud/gen1/#shelly-h-amp-t-status

    status_timestamp = status["unixtime"]
    os_timestamp = int(time.time())

    if time_is_synced(status_timestamp, os_timestamp):
        timestamp = status_timestamp
    else:
        timestamp = os_timestamp

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
                ile_shared_tools.print_(
                    f"The ShellyGen1HtReportSensorValuesHandler failed for device_ip={device_ip} "
                    f"due to unsupported path: '{self.path}'.",
                    file=sys.stderr,
                )
                return

            url_components = urllib.parse.urlparse(self.path)
            query_string = urllib.parse.parse_qs(url_components.query)
            is_valid_ht_report = "id" in query_string and "temp" in query_string and "hum" in query_string
            ile_shared_tools.print_debug(lambda: self.path)

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
                    ile_shared_tools.print_exception(exception)

                # I/O operation that may be happening after the connection is closed.
                questdb_thread = threading.Thread(
                    target=ile_shared_tools.write_ilp_to_questdb, args=(data,), daemon=False
                )
                questdb_thread.start()

            else:
                ile_shared_tools.print_(
                    f"The ShellyGen1HtReportSensorValuesHandler failed for device_ip={device_ip} "
                    f"due to unsupported query: '{self.path}'.",
                    file=sys.stderr,
                )

        except Exception as exception:
            ile_shared_tools.print_exception(exception)


# --------------------- SHELLY Gen2 -------------------------------------------


def shelly_get_gen2_device_name(device_ip: str) -> str:
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys#sysgetconfig
    sysconfig = http_call(device_ip, "rpc/Sys.GetConfig", auth=Config.shelly_gen2_auth)
    device_name: str = sysconfig["device"]["name"]
    return device_name


async def shelly_get_gen2_device_name_async(device_ip: str) -> str:
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys#sysgetconfig
    sysconfig = await http_call_async(device_ip, "rpc/Sys.GetConfig", auth=Config.shelly_gen2_auth)
    device_name: str = sysconfig["device"]["name"]
    return device_name


def shelly_get_gen2_device_status_ilp(device_ip: str, device_type: str, device_name: str) -> str:
    if device_type in (
        # Shelly Plus Plug IT
        # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-it
        "SNPL-00110IT",
        # Shelly Plus Plug S V1
        # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-s
        "SNPL-00112EU",
        # Shelly Plus Plug S V2
        # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-s-v2
        "SNPL-10112EU",
        # Shelly Plus Plug UK
        # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-uk
        "SNPL-00112UK",
        # Shelly Plus Plug US
        # https://kb.shelly.cloud/knowledge-base/shelly-plus-plug-us
        "SNPL-00116US",
        # Shelly Plug S MTR Gen3
        # https://kb.shelly.cloud/knowledge-base/shelly-plug-s-mtr-gen3
        "S3PL-00112EU",
        # Shelly Outdoor Plug S Gen3
        # https://kb.shelly.cloud/knowledge-base/outdoor-plug-s-gen3
        "S3PL-20112EU",
    ):
        # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch#status
        status = http_rpc_call(device_ip, "Switch.GetStatus", {"id": 0}, auth=Config.shelly_gen2_auth)
        return shelly_gen2_plug_status_to_ilp(device_name, status)

    ile_shared_tools.print_(
        f"The shelly_get_gen2_device_status_ilp failed for device_ip={device_ip} "
        f"due to unsupported device_type={device_type}.",
        file=sys.stderr,
    )
    return ""


def shelly_gen2_plug_status_to_ilp(device_name: str, status: dict[str, typing.Any]) -> str:
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


def shelly_gen2_plusht_status_to_ilp(device_name: str | None, status: dict[str, typing.Any]) -> str:
    # status = status in "NotifyFullStatus" notification format
    # https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/#notifyfullstatus

    # Required components: sys, devicepower:0, temperature:0, humidity:0
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/DevicePower/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Temperature/
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Humidity/

    status_timestamp = int(status["params"]["ts"] or 0)
    sys_timestamp = int(status["params"]["sys"]["unixtime"] or 0)
    os_timestamp = int(time.time())

    # https://community.shelly.cloud/topic/3444-time-from-the-past-and-an-ntp-issue-when-starting-up/
    if time_is_synced(status_timestamp, os_timestamp):
        timestamp = status_timestamp
    elif time_is_synced(sys_timestamp, os_timestamp):
        timestamp = sys_timestamp
    else:
        timestamp = os_timestamp

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
        "connect_retries=0,"
        "sensor_error=0 "
        f"{timestamp}{nano}\n"
    )


async def shelly_gen2_outbound_websocket_handler(websocket: websockets.ServerConnection) -> None:
    try:
        device_ip = websocket.remote_address[0]

        if not websocket.request:
            ile_shared_tools.print_(
                f"The shelly_gen2_outbound_websocket_handler failed for device_ip={device_ip} "
                f"due to lack of handshake request object.",
                file=sys.stderr,
            )
            return

        path = websocket.request.path
        if Config.auth_token and Config.auth_token not in path:
            ile_shared_tools.print_(
                f"The shelly_gen2_outbound_websocket_handler failed for device_ip={device_ip} "
                f"due to unsupported path '{path}'.",
                file=sys.stderr,
            )
            return

        recv = await websocket.recv()
        payload = json.loads(recv)
        ile_shared_tools.print_debug(lambda: ile_shared_tools.json_dumps(payload))

        src = payload["src"]

        # https://kb.shelly.cloud/knowledge-base/shelly-plus-h-t
        # https://kb.shelly.cloud/knowledge-base/shelly-h-t-gen3
        if src.startswith(("shellyplusht-", "shellyhtg3-")):
            # "NotifyFullStatus" messages are valuable.
            # https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/#notifyfullstatus
            # https://shelly-api-docs.shelly.cloud/gen2/General/SleepManagementForBatteryDevices
            if payload["method"] == "NotifyFullStatus":
                try:
                    # The websocket connection is still in progress. Device has active Wi-Fi.
                    device_name = await shelly_get_gen2_device_name_async(device_ip) if not Config.cloud_mode else None

                except Exception as exception:
                    ile_shared_tools.print_exception(exception)
                    return

                data = shelly_gen2_plusht_status_to_ilp(device_name, payload)

                # I/O operation that may be happening after the connection is closed.
                questdb_thread = threading.Thread(
                    target=ile_shared_tools.write_ilp_to_questdb, args=(data,), daemon=False
                )
                questdb_thread.start()

        else:
            ile_shared_tools.print_(
                f"The shelly_gen2_outbound_websocket_handler failed for device_ip={device_ip} "
                f"due to unsupported src={src}.",
                file=sys.stderr,
            )

    except Exception as exception:
        ile_shared_tools.print_exception(exception)

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

                elif device_gen in (2, 3):
                    device_name = shelly_get_gen2_device_name(device_ip)
                    data = shelly_get_gen2_device_status_ilp(device_ip, device_type, device_name)

                else:
                    data = ""
                    ile_shared_tools.print_(
                        f"The shelly_device_status_loop failed for device_ip={device_ip} "
                        f"due to unsupported device_gen={device_gen}.",
                        file=sys.stderr,
                    )

                ile_shared_tools.write_ilp_to_questdb(data)

                if sigterm_threading_event.wait(Config.scrape_interval_seconds):
                    break

                backoff_idx = -1

            if sigterm_threading_event.is_set():
                break

        except Exception as exception:
            ile_shared_tools.print_exception(exception)
            backoff_idx = max(0, min(backoff_idx + 1, len(Config.backoff_strategy_seconds) - 1))
            backoff = Config.backoff_strategy_seconds[backoff_idx]
            if sigterm_threading_event.wait(backoff):
                break


def main() -> int:
    ile_shared_tools.print_vars(Config)

    sigterm_threading_event = ile_shared_tools.configure_sigterm_handler()

    for device_ip in itertools.chain(Config.shelly_devices_ips, Config.shelly_devices_ssl_ips):
        status_thread = threading.Thread(
            target=shelly_device_status_loop,
            args=(sigterm_threading_event, device_ip),
            daemon=False,
        )
        status_thread.start()

    # Handle Shelly H&T's action: "report sensor values".
    if Config.shelly_gen1_webhook_enabled:
        # noinspection PyTypeChecker
        # (this is correct usage - https://docs.python.org/3.14/library/http.server.html#http.server.HTTPServer)
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
            await ws_server.serve_forever()

        # Horrible. Works and is compatible with sigterm_threading_event.
        websocket_server_thread = threading.Thread(
            target=lambda: asyncio.run(shelly_gen2_outbound_websocket_server()),
            daemon=True,
        )
        websocket_server_thread.start()

    ile_shared_tools.print_("STARTED", file=sys.stderr)
    sigterm_threading_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
