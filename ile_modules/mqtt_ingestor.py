#!.venv/bin/python3
"""
ILE MQTT Ingestor - Ingests MQTT messages and stores them in Valkey stream.

Subscribes to all MQTT topics (#), receives messages, and stores them
as msgpack-encoded data in a Valkey stream for downstream processing.

Message flow: MQTT broker -> on_message() -> thread pool -> Valkey stream

Supported topic patterns (messages not matching these are skipped):
See ile_tools.is_supported_topic() for the complete list.
"""

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


class Env:
    """
    Environment variables for MQTT Ingestor configuration.

    Prefix: ILE_IMI (ILE MQTT INGESTOR).
    """

    ILE_IMI_MQTT_CLIENT_ID: str = getenv("ILE_IMI_MQTT_CLIENT_ID", "mqtt-ingestor-client")
    ILE_IMI_VALKEY_STREAM_NAME: str = getenv("ILE_IMI_VALKEY_STREAM_NAME", "mqtt-ingestor-stream")
    ILE_IMI_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_MESSAGES_CNT_KEY", "mqtt-ingestor-messages")
    ILE_IMI_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IMI_VALKEY_BYTES_CNT_KEY", "mqtt-ingestor-bytes")
    ILE_IMI_VALKEY_SKIPPED_CNT_KEY: str = getenv("ILE_IMI_VALKEY_SKIPPED_CNT_KEY", "mqtt-ingestor-skipped")
    ILE_IMI_VALKEY_BATCHED_CNT_KEY: str = getenv("ILE_IMI_VALKEY_BATCHED_CNT_KEY", "mqtt-ingestor-batched")
    ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S: str = getenv("ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S", "60")
    ILE_IMI_ZIGBEE2MQTT_BATCH_INTERVAL_S: str = getenv("ILE_IMI_ZIGBEE2MQTT_BATCH_INTERVAL_S", "55")


class Config:
    """Parsed configuration from environment variables."""

    mqtt_client_id: str = Env.ILE_IMI_MQTT_CLIENT_ID

    valkey_stream_name: str = Env.ILE_IMI_VALKEY_STREAM_NAME
    valkey_messages_cnt_key: str = Env.ILE_IMI_VALKEY_MESSAGES_CNT_KEY
    valkey_bytes_cnt_key: str = Env.ILE_IMI_VALKEY_BYTES_CNT_KEY
    valkey_skipped_cnt_key: str = Env.ILE_IMI_VALKEY_SKIPPED_CNT_KEY
    valkey_batched_cnt_key: str = Env.ILE_IMI_VALKEY_BATCHED_CNT_KEY

    worker_pool_shutdown_timeout_s: float = float(Env.ILE_IMI_WORKER_POOL_SHUTDOWN_TIMEOUT_S)
    zigbee2mqtt_batch_interval_s: float = float(Env.ILE_IMI_ZIGBEE2MQTT_BATCH_INTERVAL_S)

    cpu_count: int = os.cpu_count() or 1
    max_workers: int = min(max(1, cpu_count // 2), max(cpu_count - 1, 1))


class Batcher(typing.Protocol):
    """Protocol for message batching."""

    def add_store_action(self, action: typing.Callable[[ile_tools.MqttMessage], None]) -> None:
        """Add callback for flushing messages."""
        ...

    def collect(self, topic: str, data: ile_tools.MqttMessage) -> bool:
        """Collect message. Returns True if collected, False if not handled."""
        ...

    def shutdown(self) -> None:
        """Flush remaining messages and stop background threads."""
        ...


class Zigbee2mqttBatcher:
    """
    Batcher for zigbee2mqtt device messages.

    Buffers messages per device and flushes only the newest at interval end.
    Thread-safe. Uses background thread for periodic flush.
    """

    def __init__(
        self,
        batch_interval_s: float,
        sigterm_event: threading.Event,
    ) -> None:
        self._batch_interval_s: float = batch_interval_s
        self._sigterm_event: threading.Event = sigterm_event

        self._store_actions: list[typing.Callable[[ile_tools.MqttMessage], None]] = []
        self._buffer: dict[str, ile_tools.MqttMessage] = {}  # friendly_name -> message
        self._dropped_count: int = 0
        self._lock: threading.Lock = threading.Lock()

        self._flush_thread: threading.Thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="zigbee2mqtt-batcher",
        )
        self._flush_thread.start()
        ile_tools.log_diagnostic(f"batcher: started interval={batch_interval_s}s")

    def add_store_action(self, action: typing.Callable[[ile_tools.MqttMessage], None]) -> None:
        """Add callback for flushing messages."""
        self._store_actions.append(action)

    def _flush_loop(self) -> None:
        """Flush buffered messages at regular intervals."""
        while not self._sigterm_event.is_set():
            self._sigterm_event.wait(timeout=self._batch_interval_s)
            self._flush()

    def _flush(self) -> None:
        """Flush buffer, sending newest message per device."""
        with self._lock:
            if not self._buffer:
                return
            count = len(self._buffer)
            for topic, message in self._buffer.items():
                ile_tools.log_diagnostic(f"batcher: flushing topic={topic}")
                try:
                    for store_action in self._store_actions:
                        store_action(message)
                except Exception as e:
                    ile_tools.print_exception(e, f"batcher: flush failed topic={topic}")
            self._buffer.clear()
            ile_tools.log_diagnostic(f"batcher: flushed devices={count}")

    def collect(self, topic: str, data: ile_tools.MqttMessage) -> bool:
        """Collect message. Returns True if collected, False if not a device topic."""
        if not topic.startswith("zigbee2mqtt/"):
            return False

        match = ile_tools.ZIGBEE2MQTT_DEVICE_PATTERN.match(topic)
        if not match:
            return False

        with self._lock:
            if topic in self._buffer:
                self._dropped_count += 1
            self._buffer[topic] = data
        return True

    def get_dropped_count(self) -> int:
        """Return count of dropped messages."""
        with self._lock:
            return self._dropped_count

    def shutdown(self) -> None:
        """Flush remaining messages and stop flush thread."""
        ile_tools.log_diagnostic("batcher: shutting down")
        self._flush()
        self._flush_thread.join(timeout=5.0)
        ile_tools.log_diagnostic(f"batcher: stopped dropped={self._dropped_count}")


class MqttHandler(ile_tools.MqttBaseHandler):
    """MQTT handler that stores messages in Valkey stream."""

    def __init__(
        self,
        r: valkey.Valkey,
        workers: concurrent.futures.Executor,
        batchers: typing.Sequence[Batcher],
        sigterm_event: threading.Event,
    ) -> None:
        super().__init__(sigterm_event)

        self.r: valkey.Valkey = r
        self.workers: concurrent.futures.Executor = workers
        self.batchers: typing.Sequence[Batcher] = batchers

        self.message_count: int = 0
        self.message_size: int = 0
        self.skipped_count: int = 0
        self.dropped_count: int = 0
        self._counters_lock: threading.Lock = threading.Lock()

        for batcher in self.batchers:
            batcher.add_store_action(self.submit_store)

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
        """Handle incoming MQTT message."""
        if self.sigterm_event.is_set():
            return
        try:
            data: ile_tools.MqttMessage = {
                "timestamp": time.time(),
                "topic": message.topic,
                "payload": message.payload.decode("utf-8"),
                "qos": message.qos,
                "retain": message.retain,
            }

            if not ile_tools.is_supported_topic(message.topic):
                ile_tools.log_diagnostic(f"mqtt: skipped topic={message.topic}")
                with self._counters_lock:
                    self.r.incr(Config.valkey_skipped_cnt_key, 1)
                    self.skipped_count += 1
                return

            for batcher in self.batchers:
                if batcher.collect(message.topic, data):
                    ile_tools.log_diagnostic(f"mqtt: batcher collected topic={message.topic}")
                    self.r.incr(Config.valkey_batched_cnt_key, 1)
                    self.dropped_count += 1
                    return

            self.submit_store(data)

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, f"valkey: message processing failed topic={message.topic}")
            self.sigterm_event.set()
        except Exception as e:
            ile_tools.print_exception(e, f"mqtt: message processing failed topic={message.topic}")

    def submit_store(self, data: ile_tools.MqttMessage) -> None:
        """Submit store task to thread pool."""
        future = self.workers.submit(self.store_message, data)
        future.add_done_callback(ile_tools.future_exception_callback)

    def store_message(self, data: ile_tools.MqttMessage) -> None:
        """Store message in Valkey stream. Sets sigterm_event on Valkey errors."""
        try:
            data_packed: bytes = msgpack.packb(data)
            with self.r.pipeline() as pipe:
                pipe.xadd(Config.valkey_stream_name, {"data": data_packed})
                pipe.incr(Config.valkey_messages_cnt_key, 1)
                pipe.incr(Config.valkey_bytes_cnt_key, len(data_packed))
                pipe.execute()

            with self._counters_lock:
                self.message_count += 1
                self.message_size += len(data_packed)

            debug_suffix = f" data={data}" if ile_tools.Config.debug else ""
            ile_tools.log_result(
                f"valkey: stored topic={data['topic']}"
                f" size={ile_tools.size_fmt(len(data['payload']))}"
                f" packed={ile_tools.size_fmt(len(data_packed))}"
                f"{debug_suffix}"
            )

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, f"valkey: store failed topic={data['topic']} data={data}")
            self.sigterm_event.set()
        except Exception as e:
            ile_tools.print_exception(e, f"store: unexpected error topic={data['topic']}  data={data}")


def main() -> int:
    """Entry point for MQTT ingestor. Returns 0 on success, 1 on error."""
    ile_tools.log_diagnostic("main: starting mqtt_ingestor")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    try:
        # noinspection PyTypeChecker
        with ile_tools.create_valkey_client() as r:
            ile_tools.log_diagnostic(f"main: creating thread_pool workers={Config.max_workers}")
            thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=Config.max_workers)

            zigbee2mqtt_batcher = Zigbee2mqttBatcher(
                Config.zigbee2mqtt_batch_interval_s,
                sigterm_event,
            )
            batchers: list[Batcher] = [zigbee2mqtt_batcher]

            handler = MqttHandler(r, thread_pool, batchers, sigterm_event)

            # noinspection PyTypeChecker
            with ile_tools.create_mqtt_client(handler, Config.mqtt_client_id):
                ile_tools.log_diagnostic("main: ready, waiting for messages")

                while not sigterm_event.is_set():
                    sigterm_event.wait(timeout=ile_tools.Config.sigterm_wait_s)
                ile_tools.log_diagnostic("main: shutdown signal received")

                ile_tools.log_diagnostic("main: flushing batchers")
                for batcher in batchers:
                    batcher.shutdown()

                ile_tools.log_diagnostic(f"main: stopping thread_pool timeout={Config.worker_pool_shutdown_timeout_s}s")
                if not ile_tools.shutdown_with_timeout(thread_pool, timeout=Config.worker_pool_shutdown_timeout_s):
                    ile_tools.log_diagnostic("main: thread_pool shutdown timed out, forcing")
                    thread_pool.shutdown(wait=False, cancel_futures=True)

                ile_tools.log_result(
                    f"main: finished messages={handler.message_count}"
                    f" size={ile_tools.size_fmt(handler.message_size, 'binary', 'B')}"
                    f" skipped={handler.skipped_count}"
                    f" dropped={handler.dropped_count}"
                )

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
