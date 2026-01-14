#!.venv/bin/python3

"""
ILE Payload Normalizer - Transforms MQTT messages into QuestDB rows.

Reads MQTT messages from Valkey input stream, transforms them based on
topic patterns, and writes QuestDBRow records to output stream.

Supported topic patterns:
- Shelly Gen2 status: <device_id>/status/<component>:<idx>
- Shelly Gen2 announce: <device_id>/announce
- Telegraf metrics: telegraf/<host>/<input_name>
- Zigbee2mqtt bridge devices: zigbee2mqtt/bridge/devices
- Zigbee2mqtt device messages: zigbee2mqtt/<friendly_name>

Sets sigterm_event on Valkey or MQTT connection failures.
"""

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

getenv = os.environ.get


class Env:
    """
    Environment variables for Payload Normalizer configuration.

    Prefix: ILE_IPN (ILE Payload Normalizer).
    """

    ILE_IMI_VALKEY_STREAM_NAME: str = getenv("ILE_IMI_VALKEY_STREAM_NAME", "mqtt-ingestor-stream")
    ILE_IPN_VALKEY_CONSUMER_GROUP: str = getenv("ILE_IPN_VALKEY_CONSUMER_GROUP", "payload-normalizer-consumer-group")
    ILE_IPN_VALKEY_CONSUMER_NAME: str = getenv("ILE_IPN_VALKEY_CONSUMER_NAME", "payload-normalizer-consumer-1")
    ILE_IPN_VALKEY_OUTPUT_STREAM: str = getenv("ILE_IPN_VALKEY_OUTPUT_STREAM", "payload-normalizer-stream")

    ILE_IPN_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_MESSAGES_CNT_KEY", "payload-normalizer-messages")
    ILE_IPN_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IPN_VALKEY_ROWS_CNT_KEY", "payload-normalizer-rows")
    ILE_IPN_VALKEY_BYTES_CNT_KEY: str = getenv("ILE_IPN_VALKEY_BYTES_CNT_KEY", "payload-normalizer-bytes")

    ILE_IPN_VALKEY_BATCH_SIZE: str = getenv("ILE_IPN_VALKEY_BATCH_SIZE", "1024")
    ILE_IPN_VALKEY_LOOP_WAIT_S: str = getenv("ILE_IPN_VALKEY_LOOP_WAIT_S", "10.0")
    ILE_IPN_VALKEY_LOOP_BUSY_WAIT_S: str = getenv("ILE_IPN_VALKEY_LOOP_BUSY_WAIT_S", "0.5")

    ILE_IPN_VALKEY_STREAM_TRIM_INTERVAL_S: str = getenv("ILE_IPN_VALKEY_STREAM_TRIM_INTERVAL_S", "21600")  # 6h

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

    valkey_stream_trim_interval_s: float = float(Env.ILE_IPN_VALKEY_STREAM_TRIM_INTERVAL_S)

    mqtt_client_id: str = Env.ILE_IPN_MQTT_CLIENT_ID

    shelly_gen2_announce_interval_s: float = float(Env.ILE_IPN_SHELLY_GEN2_ANNOUNCE_INTERVAL_S)
    shelly_gen2_device_name_freshness_s: float = float(Env.ILE_IPN_SHELLY_GEN2_DEVICE_NAME_FRESHNESS_S)


class ShellyGen2DeviceNames:
    """
    Cache for Shelly Gen2 device names.

    Caches names from announce messages and periodically refreshes them.
    Thread-safe via internal lock.
    """

    def __init__(self, mqtt_client: paho.mqtt.client.Client, sigterm_event: threading.Event) -> None:
        self.mqtt_client = mqtt_client
        self.sigterm_event = sigterm_event
        self.device_names: dict[str, tuple[str, float]] = {}  # device_id -> (name, timestamp_s)
        self._lock: threading.Lock = threading.Lock()

    def notify_device_seen(self, device_id: str) -> None:
        """Notify that a device_id has been seen, triggers announce if new."""
        with self._lock:
            if device_id not in self.device_names:
                self.device_names[device_id] = (device_id, 0.0)
        threading.Thread(
            target=self._send_announce,
            daemon=True,
            name=f"shelly-gen2-announce-once-{device_id}",
        ).start()

    def get_device_name(self, device_id: str) -> str:
        """Get cached device name if fresh, otherwise return device_id."""
        current_timestamp = time.time()
        with self._lock:
            if device_id in self.device_names:
                device_name, cached_timestamp = self.device_names[device_id]
                if current_timestamp - cached_timestamp <= Config.shelly_gen2_device_name_freshness_s:
                    return device_name
        return device_id

    def update_device_name(self, device_id: str, device_name: str, timestamp_s: float) -> None:
        """Update cached device name if newer than existing."""
        with self._lock:
            _, cached_timestamp_s = self.device_names.get(device_id, (device_id, 0.0))
            if timestamp_s >= cached_timestamp_s:
                self.device_names[device_id] = (device_name, timestamp_s)
                ile_tools.log_diagnostic(f"shelly_gen2: updated device_name device_id={device_id} name={device_name}")

    def announce_thread(self) -> None:
        """Background thread: periodically sends announce commands to known devices."""
        while not self.sigterm_event.is_set():
            if self.sigterm_event.wait(Config.shelly_gen2_announce_interval_s):
                break

            if not self._send_announce():
                self.sigterm_event.set()

    def _send_announce(self) -> bool:
        """Send announce command to Shelly broadcast topic. Returns True on success."""
        try:
            result = self.mqtt_client.publish("shellies/command", "announce", qos=1, retain=True)
            if result.rc != paho.mqtt.client.MQTT_ERR_SUCCESS:
                ile_tools.log_diagnostic(f"shelly_gen2: announce publish failed rc={result.rc}")
                return False
        except Exception as e:
            ile_tools.print_exception(e, "shelly_gen2: announce publish failed")
            return False
        else:
            ile_tools.log_diagnostic("shelly_gen2: announce published")
            return True


# Pre-compiled regex for normalizing table name components
_TABLE_NAME_NORMALIZE_PATTERN: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9]+")


class ZigbeeDeviceInfo:
    """
    Zigbee2mqtt device info structure.

    Stores device metadata from zigbee2mqtt/bridge/devices messages.
    Computes normalized table name from vendor and model for QuestDB.

    Attributes:
        ieee_address: IEEE device address (e.g., "0x00158d00012345").
        friendly_name: User-assigned friendly name.
        model: Device model string.
        vendor: Device vendor/manufacturer.
        model_normalized: Lowercase alphanumeric model for table name.
        vendor_normalized: Lowercase alphanumeric vendor for table name.
        table_name: QuestDB table name (e.g., "zigbee2mqtt_xiaomi_aqaratempsensor").
    """

    def __init__(self, ieee_address: str, friendly_name: str, model: str, vendor: str) -> None:
        self.ieee_address: str = ieee_address
        self.friendly_name: str = friendly_name
        self.model: str = model
        self.vendor: str = vendor

        self.model_normalized: str = _TABLE_NAME_NORMALIZE_PATTERN.sub("", model).lower()
        self.vendor_normalized: str = _TABLE_NAME_NORMALIZE_PATTERN.sub("", vendor).lower()
        self.table_name = f"zigbee2mqtt_{self.vendor_normalized}_{self.model_normalized}"

    def __repr__(self) -> str:
        return f"ZigbeeDeviceInfo({self})"

    def __str__(self) -> str:
        return (
            f"(ieee_address={self.ieee_address}, friendly_name={self.friendly_name}, "
            f"model={self.model}, vendor={self.vendor}, table_name={self.table_name})"
        )


class Zigbee2mqttDeviceMapping:
    """
    Cache for Zigbee2mqtt device mappings.

    Maps friendly_name to device info from bridge/devices messages. Thread-safe.
    """

    def __init__(self) -> None:
        self.device_mapping: dict[str, ZigbeeDeviceInfo] = {}
        self._lock: threading.Lock = threading.Lock()

    def update_devices(self, devices: list[dict[str, typing.Any]]) -> None:
        """Update device mapping from bridge/devices payload. Invalid entries are skipped."""
        with self._lock:
            for device in devices:
                try:
                    ieee_address = device["ieee_address"]
                    friendly_name = device["friendly_name"]
                    definition = device["definition"]
                    vendor = definition["vendor"]
                    model = definition["model"]

                    device_info = ZigbeeDeviceInfo(
                        ieee_address=ieee_address,
                        friendly_name=friendly_name,
                        model=model,
                        vendor=vendor,
                    )

                    self.device_mapping[friendly_name] = device_info
                    ile_tools.log_diagnostic(f"zigbee2mqtt: mapped device_info={device_info}")
                except KeyError as e:
                    ile_tools.print_exception(e, f"zigbee2mqtt: invalid device entry device={device}, skipping")

    def get_device_info(self, friendly_name: str) -> ZigbeeDeviceInfo | None:
        """Get device info for friendly_name, or None if unknown."""
        with self._lock:
            return self.device_mapping.get(friendly_name)


def parse_mqtt_message(
    mqtt_message: MqttMessage,
    shelly_gen2_device_names: ShellyGen2DeviceNames,
    zigbee2mqtt_device_mapping: Zigbee2mqttDeviceMapping,
) -> typing.Sequence[QuestDBRow]:
    """
    Parse MQTT message and transform to QuestDB rows.

    Dispatches to handler based on topic pattern. Cache-only topics return empty list.

    Raises:
        json.JSONDecodeError: If payload is not valid JSON.
        KeyError: If required payload fields are missing.
    """
    topic: str = mqtt_message["topic"]

    # Shelly Gen2 status: <device_id>/status/<component>:<idx>
    # https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Mqtt/
    if match := ile_tools.SHELLY_GEN2_STATUS_PATTERN.match(topic):
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
                timestamp_ns=ile_tools.timestamp_s_to_ns(timestamp_s),
            )
        ]

    # Shelly Gen2 announce: <device_id>/announce (updates name cache, no rows)
    if ile_tools.SHELLY_GEN2_ANNOUNCE_PATTERN.match(topic):
        timestamp_s = mqtt_message["timestamp"]
        payload = json.loads(mqtt_message["payload"])

        device_id = payload["id"]
        device_name = payload.get("name") or device_id

        shelly_gen2_device_names.update_device_name(device_id, device_name, timestamp_s)
        return []

    # Telegraf: telegraf/<host>/<input_name>
    # https://github.com/influxdata/telegraf/blob/master/plugins/outputs/mqtt/README.md
    if ile_tools.TELEGRAF_TOPIC_PATTERN.match(topic):
        timestamp_s = mqtt_message["timestamp"]

        # payload = Telegraf's MQTT Producer output, configured data_format=json + layout=batch
        payload = json.loads(mqtt_message["payload"])

        return [
            QuestDBRow(
                table=f"telegraf_{metric['name'].removeprefix('telegraf_')}",
                symbols=metric.get("tags", {}),
                columns=metric.get("fields", {}),
                timestamp_ns=ile_tools.timestamp_s_to_ns(metric["timestamp"]),
            )
            for metric in payload.get("metrics", [])
        ]

    # Zigbee2mqtt bridge devices (updates mapping cache, no rows)
    # https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqtt-bridge-devices
    if ile_tools.ZIGBEE2MQTT_BRIDGE_DEVICES_PATTERN.match(topic):
        payload = json.loads(mqtt_message["payload"])
        zigbee2mqtt_device_mapping.update_devices(payload)
        return []

    # Zigbee2mqtt device topic: zigbee2mqtt/<friendly_name>
    # https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqtt-friendly-name
    if match := ile_tools.ZIGBEE2MQTT_DEVICE_PATTERN.match(topic):
        friendly_name = match.group(1)
        device_info = zigbee2mqtt_device_mapping.get_device_info(friendly_name)

        if device_info is None:
            ile_tools.log_diagnostic(f"zigbee2mqtt: unknown device friendly_name={friendly_name}, skipping")
            return []

        timestamp_s = mqtt_message["timestamp"]
        payload = json.loads(mqtt_message["payload"])

        return [
            QuestDBRow(
                table=device_info.table_name,
                symbols={"friendly_name": friendly_name, "ieee_address": device_info.ieee_address},
                columns=ile_tools.dict_int_to_float(ile_tools.flatten_dict(payload)),
                timestamp_ns=ile_tools.timestamp_s_to_ns(timestamp_s),
            )
        ]

    ile_tools.log_diagnostic(f"normalizer: skipping topic={topic}")
    return []


def _process_message_batch(
    messages: dict[bytes, list[list[tuple[bytes, dict[bytes, bytes]]]]],
    shelly_gen2_device_names: ShellyGen2DeviceNames,
    zigbee2mqtt_device_mapping: Zigbee2mqttDeviceMapping,
) -> tuple[list[QuestDBRow], list[bytes], int]:
    """
    Unpack and transform messages from Valkey stream into QuestDB rows.

    Returns (questdb_rows, message_ids, message_count). Logs and skips failures.
    """
    batch_rows: list[QuestDBRow] = []
    batch_message_ids: list[bytes] = []
    message_count = 0

    for stream_messages in messages.values():
        for message_batch in stream_messages:
            for message_id, message_data in message_batch:
                message_count += 1
                try:
                    batch_message_ids.append(message_id)
                    data_packed = message_data[b"data"]
                    data = msgpack.unpackb(data_packed, raw=False)
                    questdb_rows = parse_mqtt_message(data, shelly_gen2_device_names, zigbee2mqtt_device_mapping)
                    batch_rows.extend(questdb_rows)

                    if ile_tools.Config.debug:
                        ile_tools.log_result(
                            f"normalizer: transformed "
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
    Write rows to output stream and acknowledge input messages atomically.

    Returns total bytes written. Raises valkey.exceptions.ValkeyError on failure.
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
    r: valkey.Valkey,
    sigterm_event: threading.Event,
    shelly_gen2_device_names: ShellyGen2DeviceNames,
    zigbee2mqtt_device_mapping: Zigbee2mqttDeviceMapping,
) -> None:
    """
    Main loop: reads input stream, transforms, writes to output stream.

    Sets sigterm_event on Valkey errors.
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

            batch_rows, batch_message_ids, message_count = _process_message_batch(
                messages, shelly_gen2_device_names, zigbee2mqtt_device_mapping
            )
            total_message_count += message_count

            try:
                message_size = _write_rows_to_valkey(r, batch_rows, batch_message_ids)
                total_message_size += message_size
                total_row_count += len(batch_rows)

            except valkey.exceptions.ValkeyError as e:
                ile_tools.print_exception(e, f"valkey: write error rows_count={len(batch_rows)} rows={batch_rows}")
                sigterm_event.set()

            if message_count >= Config.valkey_batch_size:
                sigterm_event.wait(timeout=Config.valkey_loop_busy_wait_s)
            else:
                sigterm_event.wait(timeout=Config.valkey_loop_wait_s)

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, "valkey: read error in main loop")
            sigterm_event.set()

    ile_tools.log_result(
        f"main: done messages={total_message_count} rows={total_row_count} "
        f"size={ile_tools.size_fmt(total_message_size, 'binary', 'B')}"
    )


class MqttHandler(ile_tools.MqttBaseHandler):
    """
    MQTT handler for payload normalizer.

    Only maintains connection for publishing announce commands to Shelly devices.
    Does not subscribe to any topics.
    """

    def on_message(
        self,
        client: paho.mqtt.client.Client,  # noqa: ARG002
        userdata: typing.Any,  # noqa: ANN401,ARG002
        message: paho.mqtt.client.MQTTMessage,
    ) -> None:
        """Log unexpected messages since this handler does not subscribe to topics."""
        ile_tools.log_diagnostic(f"mqtt: unexpected message topic={message.topic} (this handler does not subscribe)")


def main() -> int:
    """Entry point for payload normalizer. Returns 0 on success, 1 on error."""
    ile_tools.log_diagnostic("main: starting payload_normalizer")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    try:
        # noinspection PyTypeChecker
        with ile_tools.create_valkey_client() as r:
            if not ile_tools.ensure_consumer_group(r, Config.valkey_input_stream, Config.valkey_input_consumer_group):
                ile_tools.log_diagnostic("main: consumer_group setup failed")
                return 1

            # noinspection PyTypeChecker
            with ile_tools.create_mqtt_client(MqttHandler(sigterm_event), Config.mqtt_client_id) as mqtt_client:
                shelly_gen2_device_names = ShellyGen2DeviceNames(mqtt_client, sigterm_event)
                shelly_gen2_announce_thread = threading.Thread(
                    target=shelly_gen2_device_names.announce_thread,
                    daemon=True,
                    name="shelly-gen2-announce-thread",
                )
                shelly_gen2_announce_thread.start()
                ile_tools.log_diagnostic("main: shelly_gen2_announce_thread started")

                # Start stream trim thread
                trim_thread = threading.Thread(
                    target=ile_tools.stream_trim_thread,
                    args=(
                        r,
                        sigterm_event,
                        Config.valkey_input_stream,
                        Config.valkey_batch_size,
                        Config.valkey_stream_trim_interval_s,
                    ),
                    daemon=True,
                    name="stream-trim-thread",
                )
                trim_thread.start()
                ile_tools.log_diagnostic("main: stream_trim_thread started")

                zigbee2mqtt_device_mapping = Zigbee2mqttDeviceMapping()

                ile_tools.log_diagnostic("main: payload_normalizer ready, waiting for messages")
                process_stream_main_loop(r, sigterm_event, shelly_gen2_device_names, zigbee2mqtt_device_mapping)

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
