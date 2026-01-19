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
import pathlib
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
import yaml

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
    ILE_IPN_MQTT_CRONJOB_INIT_DELAY_S: str = getenv("ILE_IPN_MQTT_CRONJOB_INIT_DELAY_S", "60.0")
    ILE_IPN_MQTT_CRONJOB_INTERVAL_S: str = getenv("ILE_IPN_MQTT_CRONJOB_INTERVAL_S", "5.0")
    ILE_IPN_MQTT_CRONJOB_THROTTLE_S: str = getenv("ILE_IPN_MQTT_CRONJOB_THROTTLE_S", "3.0")
    ILE_IPN_MQTT_CRONJOB_PATH: str = getenv("ILE_IPN_MQTT_CRONJOB_PATH", "")


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
    mqtt_cronjob_init_delay_s: float = float(Env.ILE_IPN_MQTT_CRONJOB_INIT_DELAY_S)
    mqtt_cronjob_interval_s: float = float(Env.ILE_IPN_MQTT_CRONJOB_INTERVAL_S)
    mqtt_cronjob_throttle_s: float = float(Env.ILE_IPN_MQTT_CRONJOB_THROTTLE_S)
    mqtt_cronjob_path: str = Env.ILE_IPN_MQTT_CRONJOB_PATH


# ----------------------------------------------------------------------------------------------------------------------


class ShellyGen2DeviceInfo(typing.TypedDict):
    """
    Shelly Gen2 device info structure.

    Attributes:
        device_id: Shelly device ID.
        device_name: Shelly device name.
        device_model: Shelly device model.
    """

    device_id: str
    device_name: str
    device_model: str


class ShellyGen2Devices:
    """
    Cache for Shelly Gen2 device info.

    Maps device_id to device info from announce messages. Thread-safe.
    """

    def __init__(self) -> None:
        self.devices: dict[str, ShellyGen2DeviceInfo] = {}  # device_id -> ShellyGen2DeviceInfo
        self._lock: threading.Lock = threading.Lock()

    def notify_device_seen(self, device_id: str) -> None:
        with self._lock:
            if device_id not in self.devices:
                self.devices[device_id] = {"device_id": device_id, "device_name": device_id, "device_model": "unknown"}
                ile_tools.log_diagnostic(f"shelly_gen2: seen new device_id={device_id}")

    def get_device_by_name(self, device_id: str) -> ShellyGen2DeviceInfo | None:
        with self._lock:
            return self.devices.get(device_id)

    def get_devices_by_model(self, model: str) -> list[ShellyGen2DeviceInfo]:
        with self._lock:
            return [info for info in self.devices.values() if info["device_model"] == model]

    def get_all_devices(self) -> list[ShellyGen2DeviceInfo]:
        with self._lock:
            return list(self.devices.values())

    def update_device_info(self, device_id: str, device_name: str, model: str) -> None:
        with self._lock:
            self.devices[device_id] = {"device_id": device_id, "device_name": device_name, "device_model": model}
            ile_tools.log_diagnostic(f"shelly_gen2: updated device_id={device_id} name={device_name} model={model}")


# ----------------------------------------------------------------------------------------------------------------------

# Pre-compiled regex for normalizing table name components
_TABLE_NAME_NORMALIZE_PATTERN: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9]+")


class ZigbeeDeviceInfo(typing.TypedDict):
    """
    Zigbee2mqtt device info structure.

    Stores device metadata from zigbee2mqtt/bridge/devices messages.
    Computes normalized table name from vendor and model for QuestDB.

    Attributes:
        ieee_address: IEEE device address (e.g., "0x00158d00012345").
        friendly_name: User-assigned friendly name.
        vendor: Device vendor/manufacturer.
        model: Device model string.
        vendor_normalized: Lowercase alphanumeric vendor for table name.
        model_normalized: Lowercase alphanumeric model for table name.
        table_name: QuestDB table name (e.g., "zigbee2mqtt_xiaomi_aqaratempsensor").
    """

    ieee_address: str
    friendly_name: str
    vendor: str
    model: str


zigbee_vendor_model_to_table_name: dict[str, str] = {}  # ieee_address -> table_name


class Zigbee2mqttDevices:
    """
    Cache for Zigbee2mqtt device mappings.

    Maps friendly_name to device info from bridge/devices messages. Thread-safe.
    """

    def __init__(self) -> None:
        self.devices: dict[str, ZigbeeDeviceInfo] = {}  # friendly_name -> ZigbeeDeviceInfo
        self._lock: threading.Lock = threading.Lock()

    def update_devices(self, devices: list[dict[str, typing.Any]]) -> None:
        with self._lock:
            for device in devices:
                try:
                    ieee_address = device["ieee_address"]
                    friendly_name = device["friendly_name"]
                    definition = device["definition"]
                    vendor = definition["vendor"]
                    model = definition["model"]

                    device_info: ZigbeeDeviceInfo = {
                        "ieee_address": ieee_address,
                        "friendly_name": friendly_name,
                        "vendor": vendor,
                        "model": model,
                    }

                    vendor_normalized: str = _TABLE_NAME_NORMALIZE_PATTERN.sub("", vendor).lower()
                    model_normalized: str = _TABLE_NAME_NORMALIZE_PATTERN.sub("", model).lower()
                    table_name = f"zigbee2mqtt_{vendor_normalized}_{model_normalized}"
                    zigbee_vendor_model_to_table_name[ieee_address] = table_name

                    self.devices[friendly_name] = device_info
                    ile_tools.log_diagnostic(f"zigbee2mqtt: mapped device_info={device_info}")

                except KeyError as e:
                    ile_tools.print_exception(e, f"zigbee2mqtt: invalid device entry device={device}, skipping")

    def get_device_info(self, friendly_name: str) -> ZigbeeDeviceInfo | None:
        with self._lock:
            return self.devices.get(friendly_name)

    def get_devices_by_vendor_model(self, vendor: str, model: str) -> list[ZigbeeDeviceInfo]:
        with self._lock:
            return [info for info in self.devices.values() if info["vendor"] == vendor and info["model"] == model]

    def get_all_devices(self) -> list[ZigbeeDeviceInfo]:
        with self._lock:
            return list(self.devices.values())


# ----------------------------------------------------------------------------------------------------------------------


class MqttCronjobMessageDef(typing.TypedDict):
    """Structure for cronjob MQTT message definition from YAML file."""

    device_type: str  # "zigbee2mqtt", "shelly", or "none"
    vendor: str | None  # For zigbee2mqtt filtering
    model: str | None  # For zigbee2mqtt/shelly filtering
    topic: str  # Topic template with placeholders
    payload: typing.Any  # Payload (dict will be JSON serialized)
    qos: int  # QOS level
    retain: bool  # Retain flag
    interval_s: int  # Send interval in seconds


class MqttCronjobSender:
    """
    Periodically sends predefined MQTT messages from a YAML configuration file.

    Reads messages from YAML file at ILE_IPN_CRONJOB_FILE_PATH. Each message
    has its own interval and can target specific device types.

    Supported device_type values:
    - "zigbee2mqtt": Sends to all zigbee2mqtt devices matching vendor/model.
      Supports placeholders: <friendly_name>, <ieee_address>, <random>
    - "shelly": Sends to all Shelly Gen2 devices matching model.
      Supports placeholders: <device_name>, <device_id>, <random>
    - "none": Sends message directly without device iteration.

    YAML file format:
        messages:
          - device_type: zigbee2mqtt
            vendor: 'SONOFF'
            model: 'S60ZBTPF'
            topic: zigbee2mqtt/<friendly_name>/set
            payload: {"state": "ON"}
            qos: 1
            retain: false
            interval: 600
    """

    def __init__(
        self,
        mqtt_client: paho.mqtt.client.Client,
        sigterm_event: threading.Event,
        shelly_gen2_devices: ShellyGen2Devices,
        zigbee2mqtt_device: Zigbee2mqttDevices,
    ) -> None:
        self.mqtt_client: paho.mqtt.client.Client = mqtt_client
        self.sigterm_event: threading.Event = sigterm_event
        self.shelly_gen2_devices: ShellyGen2Devices = shelly_gen2_devices
        self.zigbee2mqtt_devices: Zigbee2mqttDevices = zigbee2mqtt_device

        self._message_defs: list[MqttCronjobMessageDef] = []
        self._last_sent: dict[int, float] = {}  # message index -> last sent timestamp
        self._lock: threading.Lock = threading.Lock()

    def load_messages_from_file(self, file_path: str) -> bool:
        """Load MQTT messages from YAML configuration file. Returns True on success."""
        try:
            with pathlib.Path(file_path).open(encoding="utf-8") as f:
                data = yaml.safe_load(f)

            messages: list[MqttCronjobMessageDef] = []
            for idx, msg in enumerate(data["messages"]):
                try:
                    device_type = msg.get("device_type", "none")
                    vendor = msg.get("vendor", "")
                    model = msg.get("model", "")
                    topic = msg["topic"]
                    payload = msg["payload"]
                    qos = int(msg.get("qos", 1))
                    retain = bool(msg.get("retain", False))
                    interval_s = int(msg.get("interval_s", 3600))

                    message_def: MqttCronjobMessageDef = {
                        "device_type": device_type,
                        "vendor": vendor,
                        "model": model,
                        "topic": topic,
                        "payload": payload,
                        "qos": qos,
                        "retain": retain,
                        "interval_s": interval_s,
                    }

                    messages.append(message_def)
                    ile_tools.log_diagnostic(
                        f"cronjob: loaded message idx={idx} device_type={device_type} "
                        f"topic={topic} interval_s={interval_s}s"
                    )
                except Exception as e:
                    ile_tools.print_exception(e, f"cronjob: failed to parse message idx={idx} msg={msg}")

            with self._lock:
                self._message_defs = messages
                self._last_sent = dict.fromkeys(range(len(messages)), 0.0)

        except FileNotFoundError:
            ile_tools.log_diagnostic(f"cronjob: file not found file_path={file_path}")
            return False
        except yaml.YAMLError as e:
            ile_tools.print_exception(e, f"cronjob: failed to parse YAML from {file_path}")
            return False
        except Exception as e:
            ile_tools.print_exception(e, f"cronjob: failed to load messages from {file_path}")
            return False
        else:
            ile_tools.log_diagnostic(f"cronjob: loaded {len(messages)} messages from {file_path}")
            return True

    def cronjob_thread(self) -> None:
        self.sigterm_event.wait(Config.mqtt_cronjob_init_delay_s)
        while not self.sigterm_event.is_set():
            if self.sigterm_event.wait(Config.mqtt_cronjob_interval_s):
                break

            current_time = time.time()
            with self._lock:
                messages_to_send = []
                for idx, msg_def in enumerate(self._message_defs):
                    interval_s = msg_def["interval_s"]
                    last_sent = self._last_sent.get(idx, 0.0)
                    if current_time - last_sent >= interval_s:
                        messages_to_send.append((idx, msg_def))
                        self._last_sent[idx] = current_time

            for idx, msg_def in messages_to_send:
                if not self._send_message(msg_def):
                    ile_tools.log_diagnostic(f"cronjob: failed to send message idx={idx}")

    def _send_message(self, msg_def: MqttCronjobMessageDef) -> bool:
        device_type = msg_def["device_type"]
        topic_template = msg_def["topic"]
        payload_raw = msg_def["payload"]
        qos = msg_def["qos"]
        retain = msg_def["retain"]

        if isinstance(payload_raw, (dict, list)):
            payload_str = json.dumps(payload_raw)
        else:
            payload_str = str(payload_raw)

        if device_type == "none":
            return self._publish_message(topic_template, payload_str, qos=qos, retain=retain)

        if device_type == "zigbee2mqtt":
            vendor = msg_def["vendor"]
            model = msg_def["model"]

            if vendor and model:
                z_devices = self.zigbee2mqtt_devices.get_devices_by_vendor_model(vendor, model)
                if not z_devices:
                    ile_tools.log_diagnostic(f"cronjob: no zigbee2mqtt devices found for vendor={vendor} model={model}")
                    return True  # Not an error, just no devices yet
            else:
                z_devices = self.zigbee2mqtt_devices.get_all_devices()
                if not z_devices:
                    ile_tools.log_diagnostic("cronjob: no zigbee2mqtt devices found")
                    return True  # Not an error, just no devices yet

            success = True
            for z_device in z_devices:
                topic = topic_template.replace("<friendly_name>", z_device["friendly_name"])
                topic = topic.replace("<ieee_address>", z_device["ieee_address"])
                topic = topic.replace("<random>", ile_tools.random_string(8))

                payload = payload_str.replace("<friendly_name>", z_device["friendly_name"])
                payload = payload.replace("<ieee_address>", z_device["ieee_address"])
                payload = payload.replace("<random>", ile_tools.random_string(8))

                if not self._publish_message(topic, payload, qos=qos, retain=retain):
                    success = False
            return success

        if device_type == "shelly":
            model = msg_def["model"]
            if model:
                s_devices = self.shelly_gen2_devices.get_devices_by_model(model)
                if not s_devices:
                    ile_tools.log_diagnostic(f"cronjob: no shelly devices found for model={model}")
                    return True  # Not an error, just no devices yet
            else:
                s_devices = self.shelly_gen2_devices.get_all_devices()
                if not s_devices:
                    ile_tools.log_diagnostic("cronjob: no shelly devices found")
                    return True  # Not an error, just no devices yet

            success = True
            for s_device in s_devices:
                topic = topic_template.replace("<device_name>", s_device["device_name"])
                topic = topic.replace("<device_id>", s_device["device_id"])
                topic = topic.replace("<random>", ile_tools.random_string(8))

                payload = payload_str.replace("<device_name>", s_device["device_name"])
                payload = payload.replace("<device_id>", s_device["device_id"])
                payload = payload.replace("<random>", ile_tools.random_string(8))

                if not self._publish_message(topic, payload, qos=qos, retain=retain):
                    success = False
            return success

        ile_tools.log_diagnostic(f"cronjob: unknown device_type={device_type}")
        return False

    def _publish_message(self, topic: str, payload: str, *, qos: int, retain: bool) -> bool:
        if self.sigterm_event.wait(Config.mqtt_cronjob_throttle_s):
            return False
        try:
            result = self.mqtt_client.publish(topic, payload, qos=qos, retain=retain)
            if result.rc != paho.mqtt.client.MQTT_ERR_SUCCESS:
                ile_tools.log_diagnostic(f"cronjob: publish failed topic={topic} rc={result.rc}")
                return False

        except Exception as e:
            ile_tools.print_exception(e, f"cronjob: publish failed topic={topic}")
            return False

        else:
            ile_tools.log_diagnostic(f"cronjob: published topic={topic} payload={payload!r}")
            return True


# ----------------------------------------------------------------------------------------------------------------------


def parse_mqtt_message(
    mqtt_message: MqttMessage,
    shelly_gen2_devices: ShellyGen2Devices,
    zigbee2mqtt_devices: Zigbee2mqttDevices,
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

        s_device_info = shelly_gen2_devices.get_device_by_name(device_id)
        if not s_device_info:
            shelly_gen2_devices.notify_device_seen(device_id)
            device_name = device_id
        else:
            device_name = s_device_info["device_name"]

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

        # payload = https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly#shellygetdeviceinfo
        payload = json.loads(mqtt_message["payload"])

        device_id = payload["id"]
        device_name = payload.get("name") or device_id
        device_model = payload.get("model", "unknown")
        shelly_gen2_devices.update_device_info(device_id, device_name, device_model)

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
        zigbee2mqtt_devices.update_devices(payload)
        return []

    # Zigbee2mqtt device topic: zigbee2mqtt/<friendly_name>
    # https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqtt-friendly-name
    if match := ile_tools.ZIGBEE2MQTT_DEVICE_PATTERN.match(topic):
        friendly_name = match.group(1)
        z_device_info = zigbee2mqtt_devices.get_device_info(friendly_name)

        if z_device_info is None:
            ile_tools.log_diagnostic(f"zigbee2mqtt: unknown device friendly_name={friendly_name}, skipping")
            return []

        timestamp_s = mqtt_message["timestamp"]
        payload = json.loads(mqtt_message["payload"])

        return [
            QuestDBRow(
                table=zigbee_vendor_model_to_table_name[z_device_info["ieee_address"]],
                symbols={"friendly_name": friendly_name, "ieee_address": z_device_info["ieee_address"]},
                columns=ile_tools.dict_int_to_float(ile_tools.flatten_dict(payload)),
                timestamp_ns=ile_tools.timestamp_s_to_ns(timestamp_s),
            )
        ]

    ile_tools.log_diagnostic(f"normalizer: skipping topic={topic}")
    return []


def _process_message_batch(
    messages: dict[bytes, list[list[tuple[bytes, dict[bytes, bytes]]]]],
    shelly_gen2_devices: ShellyGen2Devices,
    zigbee2mqtt_devices: Zigbee2mqttDevices,
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
                    questdb_rows = parse_mqtt_message(data, shelly_gen2_devices, zigbee2mqtt_devices)
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
    shelly_gen2_devices: ShellyGen2Devices,
    zigbee2mqtt_devices: Zigbee2mqttDevices,
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
                messages, shelly_gen2_devices, zigbee2mqtt_devices
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

                shelly_gen2_devices = ShellyGen2Devices()
                zigbee2mqtt_devices = Zigbee2mqttDevices()

                # Start cronjob MQTT sender thread if configured
                if Config.mqtt_cronjob_path:
                    mqtt_cronjob_sender = MqttCronjobSender(
                        mqtt_client, sigterm_event, shelly_gen2_devices, zigbee2mqtt_devices
                    )
                    if mqtt_cronjob_sender.load_messages_from_file(Config.mqtt_cronjob_path):
                        cronjob_thread = threading.Thread(
                            target=mqtt_cronjob_sender.cronjob_thread,
                            daemon=True,
                            name="cronjob-mqtt-sender-thread",
                        )
                        cronjob_thread.start()
                        ile_tools.log_diagnostic("main: cronjob_mqtt_sender_thread started")
                    else:
                        ile_tools.log_diagnostic("main: cronjob_mqtt_sender_thread not started (failed to load file)")
                        return 1
                else:
                    ile_tools.log_diagnostic("main: cronjob_mqtt_sender_thread not started (no file path)")

                ile_tools.log_diagnostic("main: payload_normalizer ready, waiting for messages")
                process_stream_main_loop(r, sigterm_event, shelly_gen2_devices, zigbee2mqtt_devices)

    except Exception as e:
        ile_tools.print_exception(e, "main: fatal error")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
