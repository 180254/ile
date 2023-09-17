import datetime
import json
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
import traceback
import typing

import openrazer.client

if typing.TYPE_CHECKING:
    import types

# ile_openrazer is a program that collects data about razer-manufactured devices and stores it to the db.
# Useful for monitoring the battery state of the devices.


class Env:
    IOR_SOCKET_TIMEOUT: str = os.environ.get("IOR_SOCKET_TIMEOUT", "10")

    IOR_QUESTDB_HOST: str = os.environ.get("IOR_QUESTDB_HOST", "")
    IOR_QUESTDB_PORT: str = os.environ.get("IOR_QUESTDB_PORT", "9009")
    IOR_QUESTDB_SSL: str = os.environ.get("IOR_QUESTDB_SSL", "false")
    IOR_QUESTDB_SSL_CAFILE: str = os.environ.get("IOR_QUESTDB_SSL_CAFILE", "")
    IOR_QUESTDB_SSL_CHECKHOSTNAME: str = os.environ.get("IOR_QUESTDB_SSL_CHECKHOSTNAME", "true")

    IOR_SCRAPE_INTERVAL: str = os.environ.get("IOR_SCRAPE_INTERVAL", "300")
    IOR_BACKOFF_STRATEGY: str = os.environ.get("IOR_BACKOFF_STRATEGY", "60,300")


class Config:
    socket_timeout_seconds: int = int(Env.IOR_SOCKET_TIMEOUT)

    questdb_address: tuple[str, int] = (Env.IOR_QUESTDB_HOST, int(Env.IOR_QUESTDB_PORT))
    questdb_ssl: bool = Env.IOR_QUESTDB_SSL.lower() == "true"
    questdb_ssl_cafile: str | None = Env.IOR_QUESTDB_SSL_CAFILE or None
    questdb_ssl_checkhostname: bool = Env.IOR_QUESTDB_SSL_CHECKHOSTNAME.lower() == "true"

    scrape_interval_seconds: int = int(Env.IOR_SCRAPE_INTERVAL)
    backoff_strategy_seconds: typing.Sequence[float] = [float(x) for x in Env.IOR_BACKOFF_STRATEGY.split(",") if x]


# noinspection DuplicatedCode
def print_(*args, **kwargs) -> None:
    """Print with timestamp."""
    timestamp = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    new_args = (timestamp, *args)
    print(*new_args, **kwargs)


# noinspection DuplicatedCode
def print_exception(exception: BaseException) -> None:
    exc_traceback: types.TracebackType | None = exception.__traceback__
    if exc_traceback:
        co_filename = exc_traceback.tb_frame.f_code.co_filename
        tb_lineno = exc_traceback.tb_lineno
        co_name = exc_traceback.tb_frame.f_code.co_name
        format_exception_only = traceback.format_exception_only(type(exception), exception)[0].strip()
        print_(f"exception: {co_filename}:{tb_lineno} ({co_name}) {format_exception_only}", file=sys.stderr)
    else:
        print_(f"exception: {exception}", file=sys.stderr)


# noinspection DuplicatedCode
def configure_sigterm_handler() -> threading.Event:
    sigterm_cnt = [0]
    sigterm_threading_event = threading.Event()

    def sigterm_handler(signal_number: int, _current_stack_frame) -> None:
        signal_name = signal.Signals(signal_number).name

        sigterm_cnt[0] += 1
        if sigterm_cnt[0] == 1:
            print_(f"Program interrupted by the {signal_name}, graceful shutdown in progress.", file=sys.stderr)
            sigterm_threading_event.set()

            for thing in threading.enumerate():
                if isinstance(thing, threading.Timer):
                    print_(f"Canceling threading.Timer: {thing}")
                    thing.cancel()
        else:
            print_(f"Program interrupted by the {signal_name} again, forced shutdown in progress.", file=sys.stderr)
            sys.exit(-1)

    for some_signal in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(some_signal, sigterm_handler)

    return sigterm_threading_event


# noinspection DuplicatedCode
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

    # Treat empty IOR_QUESTDB_HOST as print-only mode.
    if not Config.questdb_address[0]:
        return

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


def task() -> None:
    timestamp = int(time.time())
    nano = "000000000"

    data = ""
    device_manager = openrazer.client.DeviceManager()
    for device in device_manager.devices:
        device_name = device.name
        device_type = device.type
        device_serial = device.serial
        device_firmware_version = device.firmware_version
        device_driver_version = device.driver_version
        device_battery_level = device.battery_level
        device_is_charging = device.is_charging

        # symbolset
        device_name2 = device_name.replace(" ", "\\ ") if device_name else None
        device_type2 = device_type.replace(" ", "\\ ") if device_type else None
        device_serial2 = device_serial.replace(" ", "\\ ") if device_serial else None

        # columnset
        device_firmware_version2 = json.dumps(device_firmware_version) if device_firmware_version else None
        device_driver_version2 = json.dumps(device_driver_version) if device_driver_version else None
        device_battery_level2 = device_battery_level
        device_is_charging2 = device_is_charging

        data += (
            f"openrazer_info1,"
            f"device_name={device_name2},"
            f"device_type={device_type2},"
            f"device_id={device_serial2} "
            f"firmware_version={device_firmware_version2},"
            f"driver_version={device_driver_version2},"
            f"battery_level={device_battery_level2},"
            f"is_charging={device_is_charging2} "
            f"{timestamp}{nano}\n"
        )

    write_ilp_to_questdb(data)


def main() -> int:
    print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = configure_sigterm_handler()

    backoff_idx = -1
    while True:
        try:
            task()
            backoff_idx = -1

            if sigterm_threading_event.wait(Config.scrape_interval_seconds):
                break

        except Exception as exception:
            print_exception(exception)
            backoff_idx = max(0, min(backoff_idx + 1, len(Config.backoff_strategy_seconds) - 1))
            backoff = Config.backoff_strategy_seconds[backoff_idx]
            if sigterm_threading_event.wait(backoff):
                break

    return 0


if __name__ == "__main__":
    sys.exit(main())
