#!.venv/bin/python3
from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
import typing

import msgpack
import valkey
import valkey.exceptions

from ile_modules import ile_tools

if typing.TYPE_CHECKING:
    import paho.mqtt.client
    import paho.mqtt.properties
    import paho.mqtt.reasoncodes

getenv = os.environ.get

"""
ILE MQTT Ingestor - Ingests MQTT messages and stores them in Valkey stream.

Subscribes to all MQTT topics (#), receives messages, and stores them
as msgpack-encoded data in a Valkey stream for downstream processing.

Message flow: MQTT broker -> on_message() -> thread pool -> Valkey stream
"""


class Env:
    """Environment variables for MQTT Ingestor configuration. IMI = ILE MQTT INGESTOR"""

    ILE_IMI_MQTT_CLIENT_ID: str = getenv("ILE_IMI_MQTT_CLIENT_ID", "mqtt-ingestor-client")
    ILE_IMI_VALKEY_STREAM_NAME: str = getenv("ILE_IMI_VALKEY_STREAM_NAME", "mqtt-ingestor-stream")
    ILE_IMI_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_MESSAGES_CNT_KEY", "mqtt-ingestor-messages")
    ILE_IMI_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_BYTES_CNT_KEY", "mqtt-ingestor-bytes")
    ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S: str = getenv("ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S", "60")


class Config:
    """Parsed configuration from environment variables."""

    mqtt_client_id: str = Env.ILE_IMI_MQTT_CLIENT_ID

    valkey_stream_name: str = Env.ILE_IMI_VALKEY_STREAM_NAME
    valkey_messages_cnt_key: str = Env.ILE_IMI_VALKEY_MESSAGES_CNT_KEY
    valkey_bytes_cnt_key: str = Env.ILE_IMI_VALKEY_BYTES_CNT_KEY

    worker_pool_shutdown_timeout_s: float = float(Env.ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S)

    cpu_count: int = os.cpu_count() or 1
    max_workers: int = min(max(1, cpu_count // 2), max(cpu_count - 1, 1))


class MqttHandler(ile_tools.MqttBaseHandler):
    """
    MQTT message handler that stores messages in Valkey stream.

    Handles MQTT connect, subscribe, disconnect, and message events.
    Uses a thread pool for async message storage to avoid blocking the MQTT loop.
    Does not throw exceptions, logs errors internally.

    Attributes:
        r: Valkey client for stream operations.
        workers: Thread pool executor for async message storage.
        sigterm_event: Event to signal shutdown.
        message_count: Total number of messages stored.
        message_size: Total size of packed messages in bytes.
    """

    def __init__(
        self,
        r: valkey.Valkey,
        workers: concurrent.futures.Executor,
        sigterm_event: threading.Event,
    ) -> None:
        """
        Initialize the Handler.

        Args:
            r: Valkey client for stream operations.
            workers: Thread pool executor for async message storage.
            sigterm_event: Event to signal shutdown.
        """
        super().__init__(sigterm_event)

        self.r: valkey.Valkey = r
        self.workers: concurrent.futures.Executor = workers

        self.message_count: int = 0
        self.message_size: int = 0
        self.message_counters_lock = threading.Lock()

    def on_connect(
        self,
        client: paho.mqtt.client.Client,
        userdata: typing.Any,  # noqa: ANN401
        flags: paho.mqtt.client.ConnectFlags,
        reason_code: paho.mqtt.reasoncodes.ReasonCode,
        properties: paho.mqtt.properties.Properties | None,
    ) -> None:
        super().on_connect(client, userdata, flags, reason_code, properties)
        if self.sigterm_event.is_set():
            return
        ile_tools.log_diagnostic("mqtt: connected, subscribing to all topics (#)")
        client.subscribe("#", qos=2)

    def on_message(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        message: paho.mqtt.client.MQTTMessage,
    ) -> None:
        """
        Handle incoming MQTT message.

        Submits message to thread pool for async storage in Valkey.
        Decodes payload as UTF-8 but does not perform any further processing.
        Does not throw exceptions.
        """
        if self.sigterm_event.is_set():
            return
        try:
            timestamp = time.time()
            data: ile_tools.MqttMessage = {
                "timestamp": timestamp,
                "topic": message.topic,
                "payload": message.payload.decode("utf-8"),
                "qos": message.qos,
                "retain": message.retain,
            }
            future = self.workers.submit(self._store_message, data)
            future.add_done_callback(ile_tools.future_exception_callback)
        except Exception as e:
            ile_tools.print_exception(e, f"mqtt: failed to queue message topic={message.topic}")

    def _store_message(self, data: ile_tools.MqttMessage) -> None:
        """
        Store MQTT message in Valkey stream.

        Packs message using msgpack and adds to stream.
        Does not throw exceptions, sets sigterm_event on critical errors.
        """
        try:
            data_packed: bytes = msgpack.packb(data)
            with self.r.pipeline() as pipe:
                pipe.xadd(Config.valkey_stream_name, {"data": data_packed})
                pipe.incr(Config.valkey_messages_cnt_key, 1)
                pipe.incr(Config.valkey_bytes_cnt_key, len(data_packed))
                pipe.execute()

            with self.message_counters_lock:
                self.message_count += 1
                self.message_size += len(data_packed)

            debug_suffix = f" data={data}" if ile_tools.Config.debug else ""
            ile_tools.log_result(
                f"valkey: stored"
                f" topic={data['topic']}"
                f" size={ile_tools.size_fmt(len(data['payload']))}"
                f" packed={ile_tools.size_fmt(len(data_packed))}"
                f"{debug_suffix}"
            )

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, f"valkey: store failed mqtt_message={data}")
            self.sigterm_event.set()
        except Exception as e:
            ile_tools.print_exception(e, f"valkey: store failed mqtt_message={data}")


def main() -> int:
    """Entry point. Returns 0 on success, 1 on connection failure."""
    ile_tools.log_diagnostic("main: starting mqtt_ingestor")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    try:
        # noinspection PyTypeChecker
        # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
        with ile_tools.create_valkey_client() as r:
            thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=Config.max_workers)
            handler = MqttHandler(r, thread_pool, sigterm_event)

            # noinspection PyTypeChecker
            # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
            with ile_tools.create_mqtt_client(handler, Config.mqtt_client_id):
                ile_tools.log_diagnostic("main: mqtt_ingestor started, waiting for messages")

                while not sigterm_event.is_set():
                    sigterm_event.wait(timeout=ile_tools.Config.sigterm_wait_s)
                ile_tools.log_diagnostic("main: shutdown initiated")

                ile_tools.log_diagnostic("main: stopping worker pool")
                if not ile_tools.shutdown_with_timeout(thread_pool, timeout=Config.worker_pool_shutdown_timeout_s):
                    ile_tools.log_diagnostic("main: worker pool shutdown timeout, forcing")
                    thread_pool.shutdown(wait=False, cancel_futures=True)

                ile_tools.log_result(
                    f"main: done messages={handler.message_count} "
                    f"size={ile_tools.size_fmt(handler.message_size, 'binary', 'B')}"
                )

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
