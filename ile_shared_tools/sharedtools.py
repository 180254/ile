from __future__ import annotations

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

if typing.TYPE_CHECKING:
    import types
    from collections.abc import Callable

QUESTDB_EXTRA_ASYNC_SLEEP: float = 0.250


class Env:
    ILE_DEBUG: str = os.environ.get("ILE_DEBUG", "false")
    ILE_SOCKET_TIMEOUT: str = os.environ.get("ILE_SOCKET_TIMEOUT", "10")
    ILE_QUESTDB_HOST: str = os.environ.get("ILE_QUESTDB_HOST", "")
    ILE_QUESTDB_PORT: str = os.environ.get("ILE_QUESTDB_PORT", "9009")
    ILE_QUESTDB_SSL: str = os.environ.get("ILE_QUESTDB_SSL", "false")
    ILE_QUESTDB_SSL_CAFILE: str = os.environ.get("ILE_QUESTDB_SSL_CAFILE", "")
    ILE_QUESTDB_SSL_CHECKHOSTNAME: str = os.environ.get("ILE_QUESTDB_SSL_CHECKHOSTNAME", "true")


class Config:
    debug: bool = Env.ILE_DEBUG.lower() == "true"
    socket_timeout_seconds: int = int(Env.ILE_SOCKET_TIMEOUT)
    questdb_address: tuple[str, int] = (Env.ILE_QUESTDB_HOST, int(Env.ILE_QUESTDB_PORT))
    questdb_ssl: bool = Env.ILE_QUESTDB_SSL.lower() == "true"
    questdb_ssl_cafile: str | None = Env.ILE_QUESTDB_SSL_CAFILE or None
    questdb_ssl_checkhostname: bool = Env.ILE_QUESTDB_SSL_CHECKHOSTNAME.lower() == "true"


def print_(*args: str, **kwargs: typing.Any) -> None:  # noqa: ANN401
    timestamp = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    new_args = (timestamp, *args)
    print(*new_args, **kwargs)


def print_debug(msg_supplier: Callable[[], str]) -> None:
    if Config.debug:
        print_(msg_supplier())


def print_vars(obj: object) -> None:
    obj_vars = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    print_(type(obj).__name__ + str(obj_vars), file=sys.stderr)


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


def json_dumps(data: dict[str, typing.Any]) -> str:
    return json.dumps(data, separators=(",", ":"))


def configure_sigterm_handler() -> threading.Event:
    sigterm_cnt = [0]
    sigterm_threading_event = threading.Event()

    def sigterm_handler(signal_number: int, _current_stack_frame: types.FrameType | None) -> None:
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
        # https://github.com/questdb/questdb/blob/8.2.1/core/src/main/java/io/questdb/network/AbstractIODispatcher.java#L159
        time.sleep(QUESTDB_EXTRA_ASYNC_SLEEP)
        sock.sendall(b"\n")

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
