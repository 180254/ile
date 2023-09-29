import datetime
import json
import os
import signal
import sys
import threading
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import types


class Env:
    ILE_DEBUG: str = os.environ.get("ILE_DEBUG", "false")


class Config:
    debug: bool = Env.ILE_DEBUG.lower() == "true"


def print_(*args, **kwargs) -> None:
    timestamp = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    new_args = (timestamp, *args)
    print(*new_args, **kwargs)


def print_debug(msg_supplier: Callable[[], str]) -> None:
    if Config.debug:
        print_(msg_supplier())


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


def json_dumps(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"))


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
