#!.venv/bin/python3
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import typing

import msgpack
import paho.mqtt.client
import paho.mqtt.enums
import paho.mqtt.reasoncodes
import valkey
import valkey.exceptions

from ile_modules import ile_tools
from ile_modules.ile_tools import MqttMessage, QuestDBRow

"""
ILE Payload Normalizer - Transforms MQTT messages into QuestDB rows.

Reads MQTT messages from Valkey input stream, transforms them based on
topic patterns, and writes QuestDBRow records to output stream.

Supported topic patterns:
- Shelly Gen2 status: <device_id>/status/<component>:<idx>
- Shelly Gen2 announce: <device_id>/announce
- Telegraf metrics: telegraf/<host>/<input_name>

Sets sigterm_event on Valkey or MQTT connection failures.
"""

getenv = os.environ.get

TIMESTAMP_NS_MULTIPLIER: int = 1_000_000_000

# Pre-compiled regex patterns for topic matching (avoids re-compilation per message)
_SHELLY_GEN2_STATUS_PATTERN: re.Pattern[str] = re.compile(r"^([a-zA-Z0-9-]+)/status/([a-zA-Z0-9-]+):([0-9]+)$")
_SHELLY_GEN2_ANNOUNCE_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9-]+/announce$")
_TELEGRAF_TOPIC_PATTERN: re.Pattern[str] = re.compile(r"^telegraf/[^/]+/[^/]+$")


class Env:
    """Environment variable names and defaults. IPN = ILE PAYLOAD NORMALIZER."""

    ILE_IMI_VALKEY_STREAM_NAME: str = getenv("ILE_IMI_VALKEY_STREAM_NAME", "mqtt-ingestor-stream")
    ILE_IPN_VALKEY_CONSUMER_GROUP: str = getenv("ILE_IPN_VALKEY_CONSUMER_GROUP", "payload-normalizer-consumer-group")
    ILE_IPN_VALKEY_CONSUMER_NAME: str = getenv("ILE_IPN_VALKEY_CONSUMER_NAME", "payload-normalizer-consumer-1")
    ILE_IPN_VALKEY_OUTPUT_STREAM: str = getenv("ILE_IPN_VALKEY_OUTPUT_STREAM", "payload-normalizer-stream")

    ILE_IPN_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_MESSAGES_CNT_KEY", "payload-normalizer-messages")
    ILE_IPN_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IPN_VALKEY_ROWS_CNT_KEY", "payload-normalizer-rows")
    ILE_IPN_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_BYTES_CNT_KEY", "payload-normalizer-bytes")

    ILE_IPN_VALKEY_BATCH_SIZE: str = getenv("ILE_IPN_BATCH_SIZE", "1024")
    ILE_IPN_VALKEY_LOOP_WAIT_S: str = getenv("ILE_IPN_LOOP_WAIT_S", "10.0")
    ILE_IPN_VALKEY_LOOP_BUSY_WAIT_S: str = getenv("ILE_IPN_LOOP_BUSY_WAIT_S", "0.5")

    ILE_IPN_MQTT_CLIENT_ID: str = getenv("ILE_IPN_MQTT_CLIENT_ID", "payload-normalizer-client")

    ILE_IPN_SHELLY_GEN2_DEVICE_NAME_FRESHNESS_S: str = getenv(
        "ILE_IPN_SHELLY_GEN2_DEVICE_NAME_FRESHNESS_S",
        "21600",  # 6h
    )
    ILE_IPN_SHELLY_GEN2_ANNOUNCE_INTERVAL_S: str = getenv(
        "ILE_IPN_SHELLY_GEN2_ANNOUNCE_INTERVAL_S",
        "300",  # 5m
    )


class Config:
    """Parsed configuration from environment variables."""

    valkey_input_stream: str = Env.ILE_IMI_VALKEY_STREAM_NAME
    valkey_input_consumer_group: str = Env.ILE_IPN_VALKEY_CONSUMER_GROUP
    valkey_input_consumer_name: str = Env.ILE_IPN_VALKEY_CONSUMER_NAME
    valkey_output_stream: str = Env.ILE_IPN_VALKEY_OUTPUT_STREAM

    valkey_messages_cnt_key: str = Env.ILE_IPN_VALKEY_MESSAGES_CNT_KEY
    valkey_rows_cnt_key: str = Env.ILE_IPN_VALKEY_ROWS_CNT_KEY
    valkey_bytes_cnt_key: str = Env.ILE_IPN_VALKEY_BYTES_CNT_KEY

    valkey_batch_size: int = int(Env.ILE_IPN_VALKEY_BATCH_SIZE)
    valkey_loop_wait_s: float = float(Env.ILE_IPN_VALKEY_LOOP_WAIT_S)
    valkey_loop_busy_wait_s: float = float(Env.ILE_IPN_VALKEY_LOOP_BUSY_WAIT_S)

    mqtt_client_id: str = Env.ILE_IPN_MQTT_CLIENT_ID

    shelly_gen2_announce_interval_s: float = float(Env.ILE_IPN_SHELLY_GEN2_ANNOUNCE_INTERVAL_S)
    shelly_gen2_device_name_freshness_s: float = float(Env.ILE_IPN_SHELLY_GEN2_DEVICE_NAME_FRESHNESS_S)


class ShellyGen2DeviceNames:
    """
    Cache for Shelly Gen2 device names.

    Device names are not available in status topics by default from Shelly Gen2 devices.
    This class caches names received via announce messages and periodically sends
    announce commands to refresh them.
    """

    def __init__(self, mqtt_client: paho.mqtt.client.Client, sigterm_event: threading.Event) -> None:
        self.mqtt_client = mqtt_client
        self.sigterm_event = sigterm_event
        self.device_names: dict[str, tuple[str, float]] = {}  # device_id -> (name, timestamp_s)
        self._lock: threading.Lock = threading.Lock()

    def notify_device_seen(self, device_id: str) -> None:
        """
        Notify that a device_id has been seen, adding to cache if new.

        Does not throw exceptions.
        """
        with self._lock:
            if device_id not in self.device_names:
                self.device_names[device_id] = (device_id, 0.0)
                threading.Thread(
                    target=self._send_announce,
                    args=(device_id,),
                    daemon=True,
                    name=f"shelly-gen2-announce-once-{device_id}",
                ).start()

    def get_device_name(self, device_id: str) -> str:
        """
        Get cached device name if fresh, otherwise return device_id.

        Does not throw exceptions.
        """
        current_timestamp = time.time()
        with self._lock:
            if device_id in self.device_names:
                device_name, cached_timestamp = self.device_names[device_id]
                if current_timestamp - cached_timestamp <= Config.shelly_gen2_device_name_freshness_s:
                    return device_name
        return device_id

    def update_device_name(self, device_id: str, device_name: str, timestamp_s: float) -> None:
        """
        Update cached device name if newer than existing.

        Does not throw exceptions.
        """
        with self._lock:
            _, cached_timestamp_s = self.device_names.get(device_id, (device_id, 0.0))
            if timestamp_s >= cached_timestamp_s:
                self.device_names[device_id] = (device_name, timestamp_s)

    def announce_thread(self) -> None:
        """
        Background thread: periodically sends announce commands to known devices.

        Runs until sigterm_event is set.
        Sets sigterm_event on MQTT publish failure.
        """
        while not self.sigterm_event.is_set():
            if self.sigterm_event.wait(Config.shelly_gen2_announce_interval_s):
                break

            with self._lock:
                device_ids = list(self.device_names.keys())

            for device_id in device_ids:
                if self.sigterm_event.is_set():
                    break
                if not self._send_announce(device_id):
                    self.sigterm_event.set()

    def _send_announce(self, device_id: str) -> bool:
        """
        Send announce command to a device.

        Does not throw exceptions.
        """
        try:
            result = self.mqtt_client.publish(f"{device_id}/command", "announce", qos=2, retain=True)
            if result.rc != paho.mqtt.client.MQTT_ERR_SUCCESS:
                ile_tools.log_diagnostic(f"announce: publish failed device_id={device_id} rc={result.rc}")
                return False
        except Exception as e:
            ile_tools.print_exception(e, f"announce: publish failed device_id={device_id}")
            return False
        else:
            ile_tools.log_diagnostic(f"announce: published device_id={device_id}")
            return True


def parse_mqtt_message(
    mqtt_message: MqttMessage, shelly_gen2_device_names: ShellyGen2DeviceNames
) -> typing.Sequence[QuestDBRow]:
    """
    Parse MQTT message and transform to QuestDB rows.

    Returns empty list for unmatched topics.
    Raises json.JSONDecodeError or KeyError for invalid payloads.
    """
    topic: str = mqtt_message["topic"]

    # Shelly Gen2 status topic: <device_id>/status/<component>:<idx>
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Mqtt/
    if match := _SHELLY_GEN2_STATUS_PATTERN.match(topic):
        timestamp_s = mqtt_message["timestamp"]
        device_id = match.group(1)
        component = match.group(2)
        idx = match.group(3)
        device_name = shelly_gen2_device_names.get_device_name(device_id)
        if device_name == device_id:
            shelly_gen2_device_names.notify_device_seen(device_id)

        # payload = like https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch
        payload = json.loads(mqtt_message["payload"])

        return [
            QuestDBRow(
                table=f"shelly_gen2_{component}",
                symbols={"device_id": device_id, "device_name": device_name, "idx": idx},
                columns=ile_tools.flatten_dict(payload),
                timestamp_ns=int(timestamp_s) * TIMESTAMP_NS_MULTIPLIER,
            )
        ]

    # Shelly Gen2 announce topic: <device_id>/announce
    # Updates device name cache, returns no rows.
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Mqtt/#mqtt-control
    if _SHELLY_GEN2_ANNOUNCE_PATTERN.match(topic):
        timestamp_s = mqtt_message["timestamp"]

        # payload = https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly#shellygetdeviceinfo
        payload = json.loads(mqtt_message["payload"])

        device_id = payload["id"]
        device_name = payload["name"]

        shelly_gen2_device_names.update_device_name(device_id, device_name, timestamp_s)
        return []

    # Telegraf MQTT topic: telegraf/<host>/<input_name>
    # https://github.com/influxdata/telegraf/blob/master/plugins/outputs/mqtt/README.md
    if _TELEGRAF_TOPIC_PATTERN.match(topic):
        timestamp_s = mqtt_message["timestamp"]

        # payload = Telegraf's MQTT Producer output, configured data_format=json + layout=batch
        payload = json.loads(mqtt_message["payload"])

        return [
            QuestDBRow(
                table=f"telegraf_{metric['name'].removeprefix('telegraf_')}",
                symbols=metric.get("tags", {}),
                columns=metric.get("fields", {}),
                timestamp_ns=int(timestamp_s * TIMESTAMP_NS_MULTIPLIER),
            )
            for metric in payload.get("metrics", [])
        ]

    ile_tools.log_diagnostic(f"normalizer: skipping topic={topic}")
    return []


def _process_message_batch(
    messages: dict[bytes, list[list[tuple[bytes, dict[bytes, bytes]]]]],
    shelly_gen2_device_names: ShellyGen2DeviceNames,
) -> tuple[list[QuestDBRow], list[bytes], int]:
    """
    Unpack and transform messages from Valkey stream into QuestDB rows.

    Iterates over messages, unpacks msgpack data, and calls parse_mqtt_message.
    Logs and skips individual message failures without stopping.

    Returns: (rows, message_ids, message_count)
    """
    batch_rows: list[QuestDBRow] = []
    batch_message_ids: list[bytes] = []
    message_count = 0

    for stream_messages in messages.values():
        for message_backpack in stream_messages:
            message_count += 1
            for message_id, message_data in message_backpack:
                try:
                    batch_message_ids.append(message_id)
                    data_packed = message_data[b"data"]
                    data = msgpack.unpackb(data_packed, raw=False)
                    questdb_rows = parse_mqtt_message(data, shelly_gen2_device_names)
                    batch_rows.extend(questdb_rows)

                    if ile_tools.Config.debug:
                        ile_tools.log_result(
                            f"normalizer: transformed"
                            f"id={message_id!s} topic={data['topic']} rows={len(questdb_rows)} "
                            f"data={data} questdb_rows={questdb_rows}"
                        )
                    else:
                        ile_tools.log_result(
                            f"normalizer: transformed id={message_id!s} topic={data['topic']} rows={len(questdb_rows)}"
                        )
                except Exception as e:
                    ile_tools.print_exception(
                        e, f"normalizer: transform failed message_id={message_id!s} message_data={message_data}"
                    )

    return batch_rows, batch_message_ids, message_count


def _write_rows_to_valkey(
    r: valkey.Valkey,
    rows: typing.Sequence[QuestDBRow],
    message_ids: list[bytes],
) -> int:
    """
    Write rows to Valkey output stream and acknowledge input messages.

    Returns: Total size of packed data written.
    Raises: valkey.exceptions.ValkeyError on write failure.
    """
    message_size = 0
    with r.pipeline() as pipe:
        for row in rows:
            row_packed = msgpack.packb(row)
            message_size += len(row_packed)
            pipe.xadd(Config.valkey_output_stream, {"data": row_packed})

        if message_ids:
            pipe.xack(Config.valkey_input_stream, Config.valkey_input_consumer_group, *message_ids)
            pipe.incr(Config.valkey_messages_cnt_key, len(message_ids))
            pipe.incr(Config.valkey_rows_cnt_key, len(rows))
            pipe.incr(Config.valkey_bytes_cnt_key, message_size)

        pipe.execute()

    return message_size


def process_stream_main_loop(
    r: valkey.Valkey, sigterm_event: threading.Event, shelly_gen2_device_names: ShellyGen2DeviceNames
) -> None:
    """
    Main processing loop: reads input stream, transforms, writes to output stream.

    Runs until sigterm_event is set.
    Sets sigterm_event on Valkey read or write errors.
    """
    total_message_count = 0
    total_message_size = 0
    total_row_count = 0

    while not sigterm_event.is_set():
        try:
            messages = ile_tools.v_cast(
                r.xreadgroup(
                    groupname=Config.valkey_input_consumer_group,
                    consumername=Config.valkey_input_consumer_name,
                    streams={Config.valkey_input_stream: ">"},
                    count=Config.valkey_batch_size,
                    noack=False,
                )
            )

            if not messages:
                sigterm_event.wait(timeout=Config.valkey_loop_wait_s)
                continue

            batch_rows, batch_message_ids, message_count = _process_message_batch(messages, shelly_gen2_device_names)
            total_message_count += message_count

            try:
                message_size = _write_rows_to_valkey(r, batch_rows, batch_message_ids)
                total_message_size += message_size
                total_row_count += len(batch_rows)

            except valkey.exceptions.ValkeyError as e:
                ile_tools.print_exception(e, f"valkey: write error rows={batch_rows}")
                sigterm_event.set()

            if message_count >= Config.valkey_batch_size:
                sigterm_event.wait(timeout=Config.valkey_loop_busy_wait_s)
            else:
                sigterm_event.wait(timeout=Config.valkey_loop_wait_s)

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, "valkey: general error")
            sigterm_event.set()

    ile_tools.log_result(
        f"main: done messages={total_message_count} rows={total_row_count} "
        f"size={ile_tools.size_fmt(total_message_size, 'binary', 'B')}"
    )


class MqttHandler(ile_tools.MqttBaseHandler):
    """MQTT handler for payload normalizer. Does not subscribe to any topics."""

    def on_message(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        message: paho.mqtt.client.MQTTMessage,
    ) -> None:
        """Log unexpected messages since this handler does not subscribe to topics."""
        ile_tools.log_diagnostic(f"mqtt: unexpected message topic={message.topic} (this handler does not subscribe)")


def main() -> int:
    """Entry point. Returns 0 on success, 1 on fatal error."""
    ile_tools.log_diagnostic("main: starting payload_normalizer")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    try:
        # noinspection PyTypeChecker
        # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
        with ile_tools.create_valkey_client() as r:
            if not ile_tools.ensure_consumer_group(r, Config.valkey_input_stream, Config.valkey_input_consumer_group):
                ile_tools.log_diagnostic("main: consumer group setup failed")
                return 1

            # noinspection PyTypeChecker
            # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
            with ile_tools.create_mqtt_client(MqttHandler(sigterm_event), Config.mqtt_client_id) as mqtt_client:
                shelly_gen2_device_names = ShellyGen2DeviceNames(
                    mqtt_client,
                    sigterm_event,
                )
                shelly_gen2_announce_thread = threading.Thread(
                    target=shelly_gen2_device_names.announce_thread,
                    daemon=True,
                    name="shelly-gen2-announce-thread",
                )
                shelly_gen2_announce_thread.start()

                ile_tools.log_diagnostic("main: payload_normalizer started, waiting for messages")
                process_stream_main_loop(r, sigterm_event, shelly_gen2_device_names)

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
