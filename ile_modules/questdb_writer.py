#!.venv/bin/python3
from __future__ import annotations

import os
import sys
import typing

import msgpack
import questdb.ingress
import valkey
import valkey.exceptions

from ile_modules import ile_tools

if typing.TYPE_CHECKING:
    import threading

    from ile_modules.ile_tools import QuestDBRow

getenv = os.environ.get

"""
ILE QuestDB Writer - Writes QuestDB rows from Valkey stream to QuestDB.

Reads QuestDBRow records from Valkey input stream and writes to QuestDB via HTTP.
Tolerates temporary Valkey unavailability and retries automatically.
Sets sigterm_event only on QuestDB write failures.
"""


class Env:
    """Environment variables for QuestDB Writer configuration. IQW = ILE QUESTDB WRITER"""

    ILE_IQW_VALKEY_INPUT_STREAM: str = getenv("ILE_IQW_VALKEY_INPUT_STREAM", "payload-normalizer-stream")
    ILE_IQW_VALKEY_CONSUMER_GROUP: str = getenv("ILE_IQW_VALKEY_CONSUMER_GROUP", "questdb-writer-consumer-group")
    ILE_IQW_VALKEY_CONSUMER_NAME: str = getenv("ILE_IQW_VALKEY_CONSUMER_NAME", "questdb-writer-consumer-1")

    ILE_IQW_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IQW_VALKEY_MESSAGES_CNT_KEY", "questdb-writer-messages")
    ILE_IQW_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IQW_VALKEY_ROWS_CNT_KEY", "questdb-writer-rows")

    ILE_IQW_VALKEY_LOOP_WAIT_S: str = getenv("ILE_IQW_VALKEY_LOOP_WAIT_S", "10.0")
    ILE_IQW_VALKEY_LOOP_BUSY_WAIT_S: str = getenv("ILE_IQW_VALKEY_LOOP_BUSY_WAIT_S", "0.5")
    ILE_IQW_VALKEY_UNAVAILABLE_WAIT_S: str = getenv("ILE_IQW_VALKEY_UNAVAILABLE_WAIT_S", "300.0")

    ILE_IQW_QUESTDB_HOST: str = getenv("ILE_IQW_QUESTDB_HOST", "127.0.0.1")
    ILE_IQW_QUESTDB_PORT: str = getenv("ILE_IQW_QUESTDB_PORT", "9000")
    ILE_IQW_QUESTDB_SSL: str = getenv("ILE_IQW_QUESTDB_SSL", "false")
    ILE_IQW_QUESTDB_USERNAME: str = getenv("ILE_IQW_QUESTDB_USERNAME", "")
    ILE_IQW_QUESTDB_PASSWORD: str = getenv("ILE_IQW_QUESTDB_PASSWORD", "")

    ILE_IQW_BATCH_SIZE: str = getenv("ILE_IQW_BATCH_SIZE", "1024")


class Config:
    """Parsed configuration from environment variables."""

    valkey_input_stream: str = Env.ILE_IQW_VALKEY_INPUT_STREAM
    valkey_input_consumer_group: str = Env.ILE_IQW_VALKEY_CONSUMER_GROUP
    valkey_input_consumer_name: str = Env.ILE_IQW_VALKEY_CONSUMER_NAME

    valkey_messages_cnt_key: str = Env.ILE_IQW_VALKEY_MESSAGES_CNT_KEY
    valkey_rows_cnt_key: str = Env.ILE_IQW_VALKEY_ROWS_CNT_KEY

    valkey_loop_wait_s: float = float(Env.ILE_IQW_VALKEY_LOOP_WAIT_S)
    valkey_loop_busy_wait_s: float = float(Env.ILE_IQW_VALKEY_LOOP_BUSY_WAIT_S)
    valkey_unavailable_wait_s: float = float(Env.ILE_IQW_VALKEY_UNAVAILABLE_WAIT_S)

    questdb_address: tuple[str, int] = (Env.ILE_IQW_QUESTDB_HOST, int(Env.ILE_IQW_QUESTDB_PORT))
    questdb_ssl: bool = Env.ILE_IQW_QUESTDB_SSL.lower() in ("1", "true")
    questdb_username: str = Env.ILE_IQW_QUESTDB_USERNAME
    questdb_password: str = Env.ILE_IQW_QUESTDB_PASSWORD

    batch_size: int = int(Env.ILE_IQW_BATCH_SIZE)


def build_questdb_conf_string() -> str:
    """
    Build QuestDB connection configuration string.

    Constructs ILP HTTP connection string with optional SSL and auth.
    Does not throw exceptions.

    Returns: QuestDB configuration string for Sender.from_conf().
    """
    protocol = "https" if Config.questdb_ssl else "http"
    host = Config.questdb_address[0]
    port = Config.questdb_address[1]

    conf = f"{protocol}::addr={host}:{port};auto_flush=off;"

    if Config.questdb_username and Config.questdb_password:
        conf += f"username={Config.questdb_username};password={Config.questdb_password};"

    if Config.questdb_ssl:
        conf += "tls_verify=unsafe_off;"

    return conf


def write_rows_to_questdb(sender: questdb.ingress.Sender, rows: list[QuestDBRow]) -> None:
    """
    Write QuestDB rows to sender and flush.

    Args:
        sender: QuestDB sender instance.
        rows: List of QuestDBRow records to write.

    Raises:
        questdb.ingress.IngressError: On write or flush failure.
    """
    if not rows:
        return

    for row in rows:
        sender.row(
            table_name=row["table"],
            symbols=row["symbols"] or None,
            columns=row["columns"] or None,
            at=questdb.ingress.TimestampNanos(row["timestamp_ns"]),
        )

    sender.flush()

    ile_tools.log_diagnostic(f"questdb: sent {len(rows)} rows")


class _SenderManager:
    """Manages QuestDB sender lifecycle with lazy initialization."""

    def __init__(self, questdb_conf: str) -> None:
        self._questdb_conf = questdb_conf
        self._sender: questdb.ingress.Sender | None = None

    def get(self) -> questdb.ingress.Sender:
        """Get or create sender, establishing connection if needed."""
        if self._sender is None:
            ile_tools.log_diagnostic("questdb: establishing sender ...")
            self._sender = questdb.ingress.Sender.from_conf(self._questdb_conf)
            self._sender.establish()
            ile_tools.log_diagnostic("questdb: sender established")
        return self._sender

    def close(self) -> None:
        """Close sender if open, logging any errors."""
        if self._sender is not None:
            try:
                ile_tools.log_diagnostic("questdb: closing sender ...")
                self._sender.close(flush=False)
                ile_tools.log_diagnostic("questdb: sender closed")
            except Exception as e:
                ile_tools.print_exception(e, "questdb: sender close failed")
            self._sender = None


def _unpack_messages(
    messages: dict[bytes, list[list[tuple[bytes, dict[bytes, bytes]]]]],
) -> tuple[list[QuestDBRow], list[bytes]]:
    """
    Unpack messages from Valkey stream into QuestDBRow list.

    The nested structure comes from xreadgroup:
    - dict keys are stream names
    - values are lists of message batches
    - each batch contains (message_id, message_data) tuples

    Returns: (questdb_rows, message_ids)
    """
    questdb_rows: list[QuestDBRow] = []
    message_ids: list[bytes] = []

    for stream_messages in messages.values():
        for message_batch in stream_messages:
            for message_id, message_data in message_batch:
                message_ids.append(message_id)
                try:
                    data_packed = message_data[b"data"]
                    row: QuestDBRow = msgpack.unpackb(data_packed, raw=False)
                    questdb_rows.append(row)
                except (KeyError, msgpack.exceptions.UnpackException) as e:
                    ile_tools.print_exception(e, f"valkey: unpack failed id={message_id!s} data={message_data}")

    return questdb_rows, message_ids


def process_stream(r: valkey.Valkey, sigterm_event: threading.Event) -> None:
    """
    Process messages from input stream and write to QuestDB.

    Reads QuestDBRow records, batches them, and writes to QuestDB.
    Does not throw exceptions, logs errors and continues processing.
    Sets sigterm_event on fatal Valkey errors.
    """
    total_message_count = 0
    total_row_count = 0

    questdb_conf = build_questdb_conf_string()
    ile_tools.log_diagnostic(f"questdb: config={questdb_conf}")

    sender_mgr = _SenderManager(questdb_conf)

    try:
        while not sigterm_event.is_set():
            try:
                messages = ile_tools.v_cast(
                    r.xreadgroup(
                        groupname=Config.valkey_input_consumer_group,
                        consumername=Config.valkey_input_consumer_name,
                        streams={Config.valkey_input_stream: ">"},
                        count=Config.batch_size,
                        noack=False,
                    )
                )

                if not messages:
                    sigterm_event.wait(Config.valkey_loop_wait_s)
                    continue

                questdb_rows, message_ids = _unpack_messages(messages)
                message_count = len(message_ids)
                total_message_count += message_count

                if not questdb_rows:
                    sigterm_event.wait(Config.valkey_loop_wait_s)
                    continue

                try:
                    write_rows_to_questdb(sender_mgr.get(), questdb_rows)
                    total_row_count += len(questdb_rows)

                    with r.pipeline() as pipe:
                        if message_ids:
                            pipe.xack(Config.valkey_input_stream, Config.valkey_input_consumer_group, *message_ids)
                        pipe.incr(Config.valkey_messages_cnt_key, message_count)
                        pipe.incr(Config.valkey_rows_cnt_key, len(questdb_rows))
                        pipe.execute()

                    ile_tools.log_result(f"questdb: wrote messages={message_count} rows={len(questdb_rows)}")
                except questdb.ingress.IngressError as e:
                    ile_tools.print_exception(e, f"questdb: write failed rows={questdb_rows}")
                    sender_mgr.close()
                    sigterm_event.set()

                if message_count >= Config.batch_size:
                    sigterm_event.wait(Config.valkey_loop_busy_wait_s)
                else:
                    sigterm_event.wait(Config.valkey_loop_wait_s)

            except (valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError) as e:
                ile_tools.print_exception(
                    e, f"valkey: connection error, waiting {Config.valkey_unavailable_wait_s}s to retry"
                )
                sigterm_event.wait(Config.valkey_unavailable_wait_s)

            except valkey.exceptions.ValkeyError as e:
                ile_tools.print_exception(e, "valkey: valkey error")
                sigterm_event.set()

    finally:
        sender_mgr.close()

    ile_tools.log_result(f"main: done messages={total_message_count} rows={total_row_count}")


def main() -> int:
    """Entry point. Returns 0 on success. Retries Valkey connection if unavailable."""
    ile_tools.log_diagnostic("main: starting questdb_writer")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    while not sigterm_event.is_set():
        try:
            # noinspection PyTypeChecker
            # Expected type 'contextlib.AbstractContextManager', got 'Generator[Any, None, None]' instead
            with ile_tools.create_valkey_client() as r:
                if not ile_tools.ensure_consumer_group(
                    r, Config.valkey_input_stream, Config.valkey_input_consumer_group
                ):
                    ile_tools.log_diagnostic(
                        f"main: consumer group setup failed, retry in {Config.valkey_unavailable_wait_s}s"
                    )
                    sigterm_event.wait(Config.valkey_unavailable_wait_s)
                    continue

                ile_tools.log_diagnostic("main: questdb_writer started, waiting for messages")

                process_stream(r, sigterm_event)

        except (valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError) as e:
            ile_tools.print_exception(e, f"main: valkey unavailable, retry in {Config.valkey_unavailable_wait_s}s")
            sigterm_event.wait(Config.valkey_unavailable_wait_s)

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, "main: valkey error")
            sigterm_event.set()

    return 0


if __name__ == "__main__":
    sys.exit(main())
