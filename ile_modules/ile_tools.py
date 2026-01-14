#!.venv/bin/python3
"""
ILE Tools - Shared utilities for ILE modules.

Provides common utilities for all ILE modules:
- Logging with timestamps (ISO 8601 format, UTC)
- Signal handling for graceful shutdown (SIGTERM, SIGINT)
- MQTT client management (MQTTv5, TLS support)
- Valkey client management (retry logic, TLS support)
- Common type definitions (MqttMessage, QuestDBRow)
- Topic pattern matching for supported device types
"""

from __future__ import annotations

import abc
import concurrent
import contextlib
import datetime
import inspect
import json
import logging
import os
import re
import signal
import ssl
import sys
import threading
import traceback
import typing

import paho.mqtt.client
import paho.mqtt.enums
import paho.mqtt.properties
import paho.mqtt.reasoncodes
import valkey
import valkey.backoff
import valkey.exceptions
import valkey.retry

if typing.TYPE_CHECKING:
    import concurrent.futures
    import types

getenv = os.environ.get
T = typing.TypeVar("T")

logging.basicConfig(level=logging.INFO, format="%(message)s")

logger = logging.getLogger(__name__)


class Env:
    """
    Environment variables for ILE tools configuration.

    All values are strings read from environment with defaults.
    Parsed values are available in the Config class.
    """

    ILE_DEBUG: str = getenv("ILE_DEBUG", "false")

    ILE_VALKEY_HOST: str = getenv("ILE_VALKEY_HOST", "127.0.0.1")
    ILE_VALKEY_PORT: str = getenv("ILE_VALKEY_PORT", "6379")
    ILE_VALKEY_SSL: str = getenv("ILE_VALKEY_SSL", "false")
    ILE_VALKEY_DB: str = getenv("ILE_VALKEY_DB", "0")
    ILE_VALKEY_PASSWORD: str = getenv("ILE_VALKEY_PASSWORD", "")

    ILE_MQTT_HOST: str = getenv("ILE_MQTT_HOST", "127.0.0.1")
    ILE_MQTT_PORT: str = getenv("ILE_MQTT_PORT", "1883")
    ILE_MQTT_SSL: str = getenv("ILE_MQTT_SSL", "false")
    ILE_MQTT_USERNAME: str = getenv("ILE_MQTT_USERNAME", "")
    ILE_MQTT_PASSWORD: str = getenv("ILE_MQTT_PASSWORD", "")
    ILE_MQTT_KEEPALIVE_S: str = getenv("ILE_MQTT_KEEPALIVE_S", "60")

    ILE_VALKEY_SOCKET_TIMEOUT_S: str = getenv("ILE_VALKEY_SOCKET_TIMEOUT_S", "5")
    ILE_VALKEY_SOCKET_CONNECT_TIMEOUT_S: str = getenv("ILE_VALKEY_SOCKET_CONNECT_TIMEOUT_S", "15")
    ILE_VALKEY_RETRY_CAP: str = getenv("ILE_VALKEY_RETRY_CAP", "3")
    ILE_VALKEY_RETRY_BASE: str = getenv("ILE_VALKEY_RETRY_BASE", "0.15")
    ILE_VALKEY_RETRY_COUNT: str = getenv("ILE_VALKEY_RETRY_COUNT", "10")

    ILE_SIGTERM_WAIT_S: str = getenv("ILE_SIGTERM_WAIT_S", "5.0")


class Config:
    """
    Parsed configuration from environment variables.

    Class-level attributes are initialized at module load time.
    Boolean values are parsed from "1" or "true" (case-insensitive).
    """

    debug: bool = Env.ILE_DEBUG.lower() in ("1", "true")

    valkey_host: str = Env.ILE_VALKEY_HOST
    valkey_port: int = int(Env.ILE_VALKEY_PORT)
    valkey_ssl: bool = Env.ILE_VALKEY_SSL.lower() in ("1", "true")
    valkey_db: int = int(Env.ILE_VALKEY_DB)
    valkey_password: str = Env.ILE_VALKEY_PASSWORD

    mqtt_host: str = Env.ILE_MQTT_HOST
    mqtt_port: int = int(Env.ILE_MQTT_PORT)
    mqtt_ssl: bool = Env.ILE_MQTT_SSL.lower() in ("1", "true")
    mqtt_username: str = Env.ILE_MQTT_USERNAME
    mqtt_password: str = Env.ILE_MQTT_PASSWORD
    mqtt_keepalive_s: int = int(Env.ILE_MQTT_KEEPALIVE_S)

    valkey_socket_timeout_s: float = float(Env.ILE_VALKEY_SOCKET_TIMEOUT_S)
    valkey_socket_connect_timeout_s: float = float(Env.ILE_VALKEY_SOCKET_CONNECT_TIMEOUT_S)
    valkey_retry_cap: float = float(Env.ILE_VALKEY_RETRY_CAP)
    valkey_retry_base: float = float(Env.ILE_VALKEY_RETRY_BASE)
    valkey_retry_count: int = int(Env.ILE_VALKEY_RETRY_COUNT)

    sigterm_wait_s: float = float(Env.ILE_SIGTERM_WAIT_S)


def print_(msg: str, file: typing.TextIO = sys.stdout) -> None:
    """Print message with ISO timestamp prefix (UTC)."""
    timestamp = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    new_msg = f"{timestamp} {msg}"
    logger.info(new_msg)
    # print(new_msg, file=file)


def log_result(msg: str) -> None:
    """Log result message to stdout with timestamp. Use for operational results and metrics."""
    print_(msg)


def log_diagnostic(msg: str) -> None:
    """Log diagnostic message to stderr with timestamp. Use for startup, shutdown, and events."""
    print_(msg, file=sys.stderr)


def print_vars(obj: object) -> None:
    """
    Print all public attributes of an object or class to stderr.

    Useful for logging configuration at startup. Excludes private attributes.

    Args:
        obj: Object instance or class to inspect.
    """
    if isinstance(obj, type):
        class_name = obj.__name__
        attrs = vars(obj).items()
    else:
        class_name = type(obj).__name__
        attrs = {**vars(obj.__class__), **vars(obj)}.items()
    pub_attrs = {key: value for key, value in attrs if not key.startswith("_")}
    print(f"{class_name} {pub_attrs}", file=sys.stderr)


def print_exception(exception: BaseException, message: str = "") -> None:
    """Print exception details with timestamp to stderr. Includes file, line, function, and message."""
    exc_traceback: types.TracebackType | None = exception.__traceback__
    prefix = f"{message}: " if message else ""
    if exc_traceback:
        co_filename = exc_traceback.tb_frame.f_code.co_filename
        tb_lineno = exc_traceback.tb_lineno
        co_name = exc_traceback.tb_frame.f_code.co_name
        format_exception_only = traceback.format_exception_only(type(exception), exception)[0].strip()
        log_diagnostic(f"EXCEPTION: {prefix}{co_filename}:{tb_lineno} ({co_name}) {format_exception_only}")
    else:
        log_diagnostic(f"EXCEPTION: {prefix}{exception}")


def json_dumps(data: dict[str, typing.Any]) -> str:
    """
    Serialize dict to compact JSON string without whitespace.

    Args:
        data: Dictionary to serialize.

    Returns:
        Compact JSON string.

    Raises:
        TypeError: If data contains non-serializable types.
        ValueError: If data contains circular references.
    """
    return json.dumps(data, separators=(",", ":"))


# https://stackoverflow.com/a/1094933/8574922
def size_fmt(num: float, mode: typing.Literal["metric", "binary"] = "metric", suffix: str = "") -> str:
    """
    Convert number to human-readable size string.

    Args:
        num: The number to format.
        mode: "metric" uses base-1000 (K, M, G), "binary" uses base-1024 (Ki, Mi, Gi).
        suffix: Optional suffix to append (e.g., "B" for bytes).

    Returns:
        Formatted string like "12.3K", "1.2GiB", "2.3M".
    """
    base = 1024.0 if mode == "binary" else 1000.0
    i = "i" if mode == "binary" and num >= base else ""
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < base:
            return f"{num:3.1f}{unit}{i}{suffix}"
        num /= base
    return f"{num:.1f}Y{i}{suffix}"


def v_cast(x: typing.Awaitable[T] | T) -> T:
    """
    Cast away awaitable type for static type checkers.

    Workaround for valkey-py typing issues where methods return Awaitable[T] | T.
    See: https://github.com/valkey-io/valkey-py/issues/84

    Raises:
        TypeError: If an awaitable is passed (async client not supported).
    """
    if inspect.isawaitable(x):
        msg = "v_cast() received an awaitable."
        raise TypeError(msg)
    return x


def shutdown_with_timeout(pool: concurrent.futures.Executor, timeout: float) -> bool:
    """Shutdown executor pool with timeout. Returns True if shutdown completed within timeout."""
    log_diagnostic(f"executor: shutting down, timeout={timeout}s")
    shutdown_complete = threading.Event()

    def do_shutdown() -> None:
        pool.shutdown(wait=True, cancel_futures=False)
        shutdown_complete.set()

    t = threading.Thread(target=do_shutdown, daemon=True)
    t.start()

    result = shutdown_complete.wait(timeout=timeout)
    if result:
        log_diagnostic("executor: shutdown completed")
    else:
        log_diagnostic(f"executor: shutdown timed out after {timeout}s")
    return result


def configure_sigterm_handler() -> threading.Event:
    """
    Configure SIGTERM/SIGINT handlers for graceful shutdown.

    First signal sets the returned event, second signal forces immediate exit.

    Returns:
        Event that is set when shutdown is requested.
    """
    sigterm_threading_event = threading.Event()

    class SigtermHandler:
        def __init__(self) -> None:
            self.sigterm_cnt = 0

        def __call__(self, signal_number: int, _current_stack_frame: types.FrameType | None) -> None:
            signal_name = signal.Signals(signal_number).name

            self.sigterm_cnt += 1
            if self.sigterm_cnt == 1:
                log_diagnostic(f"shutdown: interrupted by {signal_name}, graceful shutdown in progress")
                sigterm_threading_event.set()

            else:
                log_diagnostic(f"shutdown: interrupted by {signal_name} again, forced shutdown")
                sys.exit(-1)

    handler = SigtermHandler()
    for some_signal in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(some_signal, handler)

    return sigterm_threading_event


def future_exception_callback[T](future: concurrent.futures.Future[T]) -> None:
    """Callback for futures to log exceptions. Use with future.add_done_callback()."""
    if exc := future.exception():
        print_exception(exc)


def flatten_dict(data: dict[str, typing.Any], recursive_key: str = "", sep: str = "_") -> dict[str, typing.Any]:
    """
    Flatten nested dictionary into single-level with concatenated keys.

    Lists are flattened by index (key_0, key_1, ...).
    Special case: "errors" list values are joined as comma-separated string.

    Example:
        >>> flatten_dict({"a": {"b": 1, "c": 2}})
        {"a_b": 1, "a_c": 2}
    """
    flat: dict[str, typing.Any] = {}
    _flatten_dict_into(data, flat, recursive_key, sep)
    return flat


def _flatten_dict_into(
    data: dict[str, typing.Any],
    flat: dict[str, typing.Any],
    recursive_key: str,
    sep: str,
) -> None:
    """Internal helper: flattens data into flat dict in-place."""
    for key, value in data.items():
        new_key = f"{recursive_key}{sep}{key}" if recursive_key else key

        # Special case: errors list -> comma-separated string
        if key == "errors" and isinstance(value, list):
            flat[key] = ",".join(map(str, value))

        # Dict: recurse
        elif isinstance(value, dict):
            _flatten_dict_into(value, flat, new_key, sep)

        # List: flatten by index
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    _flatten_dict_into(item, flat, f"{new_key}{sep}{i}", sep)
                else:
                    flat[f"{new_key}{sep}{i}"] = item

        # Primitive value
        else:
            flat[new_key] = value


def dict_int_to_float(data: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """Convert all integer values in a dictionary to floats. Other types unchanged."""
    return {key: float(value) if isinstance(value, int) else value for key, value in data.items()}


TIMESTAMP_NS_MULTIPLIER: int = 1_000_000_000


def timestamp_s_to_ns(timestamp_s: float) -> int:
    return int(timestamp_s * TIMESTAMP_NS_MULTIPLIER)


class MqttMessage(typing.TypedDict):
    """MQTT message structure: timestamp, topic, payload, qos, retain."""

    timestamp: float
    topic: str
    payload: str
    qos: int
    retain: bool


class QuestDBRow(typing.TypedDict):
    """QuestDB ILP row structure: table, symbols, columns, timestamp_ns."""

    table: str
    symbols: dict[str, typing.Any]
    columns: dict[str, typing.Any]
    timestamp_ns: int


@contextlib.contextmanager
def create_valkey_client() -> typing.Generator[valkey.Valkey]:
    """
    Create Valkey client context manager with retry logic and SSL support.

    Raises:
        valkey.exceptions.ValkeyError: If connection verification fails.
    """
    log_diagnostic(f"valkey: connecting server={Config.valkey_host}:{Config.valkey_port} ssl={Config.valkey_ssl}")
    r: valkey.Valkey | None = None
    try:
        r = valkey.Valkey(
            host=Config.valkey_host,
            port=Config.valkey_port,
            db=Config.valkey_db,
            password=Config.valkey_password or None,
            socket_timeout=Config.valkey_socket_timeout_s,
            socket_connect_timeout=Config.valkey_socket_connect_timeout_s,
            decode_responses=False,
            ssl=Config.valkey_ssl,
            ssl_ca_certs=None,
            ssl_check_hostname=False,
            ssl_cert_reqs="none",
            # https://valkey-py.readthedocs.io/en/stable/retry.html
            retry=valkey.retry.Retry(
                backoff=valkey.backoff.EqualJitterBackoff(
                    cap=Config.valkey_retry_cap,
                    base=Config.valkey_retry_base,
                ),
                retries=Config.valkey_retry_count,
            ),
            retry_on_error=[
                valkey.exceptions.BusyLoadingError,
                valkey.exceptions.ConnectionError,
                valkey.exceptions.TimeoutError,
            ],
            protocol=3,
        )

        try:
            log_diagnostic("valkey: client created, verifying connection")
            r.incr("ile-init", 1)
        except valkey.exceptions.ValkeyError as e:
            print_exception(e, "valkey: connect failed")
            raise
        else:
            log_diagnostic("valkey: connected")

        yield r

    finally:
        if r:
            try:
                log_diagnostic("valkey: closing client")
                r.close()
            except Exception as e:
                print_exception(e, "valkey: client close failed")
            else:
                log_diagnostic("valkey: client closed")


def ensure_consumer_group(
    r: valkey.Valkey,
    stream_name: str,
    consumer_group: str,
) -> bool:
    """
    Ensure Valkey consumer group exists, creating it if needed.

    Returns:
        True if consumer group exists or was created, False on error.
    """
    try:
        log_diagnostic(f"valkey: creating consumer group={consumer_group} stream={stream_name}")
        r.xgroup_create(
            name=stream_name,
            groupname=consumer_group,
            id="0",
            mkstream=True,
        )

    except valkey.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            log_diagnostic(f"valkey: consumer already exists group={consumer_group} stream={stream_name}")
            return True
        print_exception(e, f"valkey: consumer create failed group={consumer_group} stream={stream_name}")
        return False

    except valkey.exceptions.ValkeyError as e:
        print_exception(e, f"valkey: consumer create failed group={consumer_group} stream={stream_name}")
        return False

    log_diagnostic(f"valkey: consumer created group={consumer_group} stream={stream_name}")
    return True


class MqttBaseHandler(abc.ABC):
    """
    Base class for MQTT message handling.

    Provides default connect/subscribe/disconnect callbacks.
    Subclasses must implement on_message(). Sets sigterm_event on connection failure.
    """

    def __init__(
        self,
        sigterm_event: threading.Event,
    ) -> None:
        """Initialize with sigterm_event for shutdown signaling on errors."""
        self.sigterm_event: threading.Event = sigterm_event

    def on_connect(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        flags: paho.mqtt.client.ConnectFlags,  # noqa: ARG002
        reason_code: paho.mqtt.reasoncodes.ReasonCode,
        properties: paho.mqtt.properties.Properties | None,  # noqa: ARG002
    ) -> None:
        """Handle connect callback. Sets sigterm_event on failure."""
        if reason_code.is_failure:
            log_diagnostic(f"mqtt: connect failed code={reason_code}")
            self.sigterm_event.set()
        else:
            log_diagnostic(f"mqtt: connected code={reason_code}")

    def on_subscribe(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        mid: int,
        reason_code_list: list[paho.mqtt.reasoncodes.ReasonCode],
        properties: paho.mqtt.properties.Properties | None,  # noqa: ARG002
    ) -> None:
        """Handle subscribe callback. Sets sigterm_event on failure."""
        is_failure = any(rc.is_failure for rc in reason_code_list)
        if is_failure:
            log_diagnostic(f"mqtt: subscribe failed mid={mid} codes={reason_code_list}")
            self.sigterm_event.set()
        else:
            log_diagnostic(f"mqtt: subscribed mid={mid} codes={reason_code_list}")

    def on_disconnect(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        flags: paho.mqtt.client.DisconnectFlags,  # noqa: ARG002
        reason_code: paho.mqtt.reasoncodes.ReasonCode,
        properties: paho.mqtt.properties.Properties | None,  # noqa: ARG002
    ) -> None:
        """Handle disconnect callback. Sets sigterm_event on unexpected disconnect."""
        if reason_code.is_failure:
            log_diagnostic(f"mqtt: disconnected unexpectedly code={reason_code}")
            self.sigterm_event.set()
        else:
            log_diagnostic(f"mqtt: disconnected code={reason_code}")

    @abc.abstractmethod
    def on_message(
        self,
        client: paho.mqtt.client.Client,
        userdata: typing.Any,  # noqa: ANN401
        message: paho.mqtt.client.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message. Subclasses must implement."""
        ...


@contextlib.contextmanager
def create_mqtt_client(handler: MqttBaseHandler, client_id: str) -> typing.Generator[paho.mqtt.client.Client]:
    """
    Create MQTT client context manager with MQTTv5 and optional SSL/TLS.

    Starts network loop on entry, stops on exit.

    Raises:
        Exception: If initial connection fails.
    """
    mqtt_client: paho.mqtt.client.Client | None = None
    try:
        mqtt_client = paho.mqtt.client.Client(
            callback_api_version=paho.mqtt.enums.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            userdata=None,
            protocol=paho.mqtt.enums.MQTTProtocolVersion.MQTTv5,
            transport="tcp",
            reconnect_on_failure=True,
            manual_ack=False,
        )

        mqtt_client.on_connect = handler.on_connect
        mqtt_client.on_subscribe = handler.on_subscribe
        mqtt_client.on_disconnect = handler.on_disconnect
        mqtt_client.on_message = handler.on_message

        mqtt_client.username_pw_set(Config.mqtt_username, Config.mqtt_password)

        if Config.mqtt_ssl:
            mqtt_ssl_context = ssl.create_default_context()
            mqtt_ssl_context.check_hostname = False
            mqtt_ssl_context.verify_mode = ssl.CERT_NONE
            mqtt_client.tls_set_context(mqtt_ssl_context)

        log_diagnostic(f"mqtt: connecting to {Config.mqtt_host}:{Config.mqtt_port}, ssl={Config.mqtt_ssl}")

        try:
            mqtt_client.connect(
                Config.mqtt_host,
                Config.mqtt_port,
                keepalive=Config.mqtt_keepalive_s,
            )
        except Exception as e:
            print_exception(e, "mqtt: connection failed")
            raise

        log_diagnostic("mqtt: starting client loop")
        ret = mqtt_client.loop_start()
        log_diagnostic(f"mqtt: client loop started, result={ret}")

        if mqtt_client:
            yield mqtt_client

    finally:
        if mqtt_client:
            log_diagnostic("mqtt: stopping client loop")
            ret = mqtt_client.loop_stop()
            log_diagnostic(f"mqtt: client loop stopped, result={ret}")
            ret = mqtt_client.disconnect()
            log_diagnostic(f"mqtt: disconnected, result={ret}")


def stream_trim_thread(
    r: valkey.Valkey,
    sigterm_event: threading.Event,
    input_stream: str,
    batch_size: int,
    interval_s: float,
) -> None:
    """
    Background thread: periodically trims the input stream to manage memory.

    Calculates entries to keep as: (pending + lag + batch_size) * 10.
    Runs every stream_trim_interval_s seconds.
    """
    while not sigterm_event.is_set():
        try:
            groups_info = v_cast(r.xinfo_groups(input_stream))
            pending = 0
            lag = 0

            for group in groups_info:
                pending = max(pending, group.get("pending", 0) or 0)
                lag = max(pending, group.get("lag", 0) or 0)

            min_entries = (pending + lag + batch_size) * 10
            trimmed = r.xtrim(input_stream, maxlen=min_entries, approximate=True, limit=0)

            log_diagnostic(
                f"stream_trim: stream={input_stream} "
                f"trimmed={trimmed} pending={pending} lag={lag} min_entries={min_entries}"
            )

        except valkey.exceptions.ValkeyError as e:
            print_exception(e, "stream_trim: failed to trim stream")
            # Don't set sigterm_event - this is a non-fatal error, will retry next interval

        if sigterm_event.wait(interval_s):
            break


# Pre-compiled regex patterns for topic matching (shared between mqtt_ingestor and payload_normalizer)
SHELLY_GEN2_STATUS_PATTERN: re.Pattern[str] = re.compile(r"^(shelly[a-zA-Z0-9-]+)/status/([a-zA-Z0-9-]+):([0-9]+)$")
SHELLY_GEN2_ANNOUNCE_PATTERN: re.Pattern[str] = re.compile(r"^shelly[a-zA-Z0-9-]+/announce$")
TELEGRAF_TOPIC_PATTERN: re.Pattern[str] = re.compile(r"^telegraf/[^/]+/[^/]+$")
ZIGBEE2MQTT_BRIDGE_DEVICES_PATTERN: re.Pattern[str] = re.compile(r"^zigbee2mqtt/bridge/devices$")
ZIGBEE2MQTT_DEVICE_PATTERN: re.Pattern[str] = re.compile(r"^zigbee2mqtt/([^/]+)$")


def is_supported_topic(topic: str) -> bool:
    """Check if topic matches a supported pattern.

    Supported topic patterns:
        - Shelly Gen2 status: <device_id>/status/<component>:<idx>
        - Shelly Gen2 announce: <device_id>/announce
        - Telegraf metrics: telegraf/<host>/<input_name>
        - Zigbee2mqtt bridge devices: zigbee2mqtt/bridge/devices
        - Zigbee2mqtt device messages: zigbee2mqtt/<friendly_name>
    """
    if topic.startswith("telegraf/"):
        return TELEGRAF_TOPIC_PATTERN.match(topic) is not None
    if topic.startswith("zigbee2mqtt/"):
        return (
            ZIGBEE2MQTT_BRIDGE_DEVICES_PATTERN.match(topic) is not None
            or ZIGBEE2MQTT_DEVICE_PATTERN.match(topic) is not None
        )
    if topic.startswith("shelly"):
        return (
            SHELLY_GEN2_STATUS_PATTERN.match(topic) is not None or SHELLY_GEN2_ANNOUNCE_PATTERN.match(topic) is not None
        )
    return False
