#!.venv/bin/python3
from __future__ import annotations

import os
import sys
import typing

import valkey
import valkey.exceptions

from ile_modules import ile_tools

getenv = os.environ.get

"""
ILE Report Printer - Reports the state of data in Valkey.

Periodically prints:
- Valkey server info
- ILE status counters (messages, rows, bytes)
- Stream lengths and consumer group status

Sets sigterm_event on Valkey errors.
"""


class Env:
    """Environment variables for Report Writer configuration. IRW = ILE REPORT PRINTER."""

    # Stream names
    ILE_IMI_VALKEY_STREAM_NAME: str = getenv("ILE_IMI_VALKEY_STREAM_NAME", "mqtt-ingestor-stream")
    ILE_IPN_VALKEY_OUTPUT_STREAM: str = getenv("ILE_IPN_VALKEY_OUTPUT_STREAM", "payload-normalizer-stream")

    # Counter keys from mqtt_ingestor
    ILE_IMI_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_MESSAGES_CNT_KEY", "mqtt-ingestor-messages")
    ILE_IMI_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_BYTES_CNT_KEY", "mqtt-ingestor-bytes")

    # Counter keys from payload_normalizer
    ILE_IPN_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_MESSAGES_CNT_KEY", "payload-normalizer-messages")
    ILE_IPN_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IPN_VALKEY_ROWS_CNT_KEY", "payload-normalizer-rows")
    ILE_IPN_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_BYTES_CNT_KEY", "payload-normalizer-bytes")

    # Counter keys from questdb_writer
    ILE_IQW_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IQW_VALKEY_MESSAGES_CNT_KEY", "questdb-writer-messages")
    ILE_IQW_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IQW_VALKEY_ROWS_CNT_KEY", "questdb-writer-rows")

    # Config
    ILE_IRP_LOG_INTERVAL_S: str = getenv("ILE_IRP_LOG_INTERVAL_S", "60")


class Config:
    """Parsed configuration from environment variables."""

    counters: typing.Sequence[str] = [
        Env.ILE_IMI_VALKEY_MESSAGES_CNT_KEY,
        Env.ILE_IMI_VALKEY_BYTES_CNT_KEY,
        Env.ILE_IPN_VALKEY_MESSAGES_CNT_KEY,
        Env.ILE_IPN_VALKEY_ROWS_CNT_KEY,
        Env.ILE_IPN_VALKEY_BYTES_CNT_KEY,
        Env.ILE_IQW_VALKEY_MESSAGES_CNT_KEY,
        Env.ILE_IQW_VALKEY_ROWS_CNT_KEY,
    ]

    streams: typing.Sequence[str] = [
        Env.ILE_IMI_VALKEY_STREAM_NAME,
        Env.ILE_IPN_VALKEY_OUTPUT_STREAM,
    ]

    log_interval_s: float = float(Env.ILE_IRP_LOG_INTERVAL_S)


def _log(label: str, value: object, width: int = 36, indent: int = 2) -> None:
    ile_tools.log_result(f"{' ' * indent}{label.ljust(width)}{value}")


def _log_size(label: str, size: int, width: int = 36, indent: int = 2) -> None:
    _log(label, ile_tools.size_fmt(size, "binary", "B"), width, indent)


def print_server_info(r: valkey.Valkey) -> None:
    info = ile_tools.v_cast(r.info())
    _log("redis_version", info.get("redis_version", "N/A"))
    _log("uptime_in_seconds", f"{info.get('uptime_in_seconds', 0)}s", indent=4)
    _log("connected_clients", info.get("connected_clients", 0), indent=4)
    _log_size("used_memory", info.get("used_memory", 0), indent=4)
    _log_size("used_memory_peak", info.get("used_memory_peak", 0), indent=4)
    _log("total_connections_received", info.get("total_connections_received", 0), indent=4)
    _log("total_commands_processed", info.get("total_commands_processed", 0), indent=4)
    _log_size("total_net_input_bytes", info.get("total_net_input_bytes", 0), indent=4)
    _log_size("total_net_output_bytes", info.get("total_net_output_bytes", 0), indent=4)
    _log("db0", info.get("db0", {}), indent=4)


def print_counters(r: valkey.Valkey) -> None:
    _log("ile_counters", "")
    for key in Config.counters:
        value = int(ile_tools.v_cast(r.get(key)) or 0)
        if "bytes" in key:
            _log(key, f"{value} ({ile_tools.size_fmt(value, 'binary', 'B')})", indent=4)
        else:
            _log(key, value, indent=4)


def print_stream_info(r: valkey.Valkey) -> None:
    for stream_name in Config.streams:
        stream_length = int(ile_tools.v_cast(r.xlen(stream_name)))
        _log(f"stream={stream_name}", f"xlen={stream_length}")

        try:
            groups = ile_tools.v_cast(r.xinfo_groups(stream_name))
            for group in groups:
                group_name = group.get("name", b"").decode("utf-8")
                _log("consumer_group", group_name, indent=4)
                _log("pending", group.get("pending", 0), indent=6)
                _log("lag", group.get("lag", 0), indent=6)
                _log("consumers", group.get("consumers", 0), indent=6)
                _log("last_delivered_id", group.get("last-delivered-id", b"").decode("utf-8"), indent=6)

                consumers = ile_tools.v_cast(r.xinfo_consumers(stream_name, group_name))
                for consumer in consumers:
                    name = consumer.get("name", b"").decode("utf-8")
                    pending = consumer.get("pending", 0)
                    idle_ms = consumer.get("idle", 0)
                    _log("consumer", f"name={name} pending={pending} idle={idle_ms}ms", indent=6)
        except valkey.exceptions.ValkeyError:
            ile_tools.print_debug(lambda stream_name2=stream_name: f"valkey: xinfo_groups failed stream={stream_name2}")


def main() -> int:
    """Entry point. Returns 0 on success, 1 on fatal error."""
    ile_tools.log_diagnostic("main: starting report_printer")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    try:
        # noinspection PyTypeChecker
        # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
        with ile_tools.create_valkey_client() as r:
            while not sigterm_event.is_set():
                try:
                    print_server_info(r)
                    print_counters(r)
                    print_stream_info(r)
                    ile_tools.log_result("---")
                except valkey.exceptions.ValkeyError as e:
                    ile_tools.print_exception(e, "main: valkey error")
                    sigterm_event.set()
                    break

                sigterm_event.wait(Config.log_interval_s)

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
