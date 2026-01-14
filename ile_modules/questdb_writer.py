#!.venv/bin/python3
"""
ILE QuestDB Writer - Writes QuestDB rows from Valkey stream to QuestDB.

Reads QuestDBRow records from Valkey input stream and writes to QuestDB via HTTP ILP.
Tolerates temporary Valkey connection errors and retries automatically.
Sets sigterm_event on QuestDB write failures or fatal Valkey errors.
"""

from __future__ import annotations

import os
import sys
import threading
import typing

import msgpack
import questdb.ingress
import valkey
import valkey.exceptions

from ile_modules import ile_tools

if typing.TYPE_CHECKING:
    from ile_modules.ile_tools import QuestDBRow

getenv = os.environ.get


class Env:
    """
    Environment variables for QuestDB Writer configuration.

    Prefix: ILE_IQW (ILE QuestDB Writer).
    """

    ILE_IQW_VALKEY_INPUT_STREAM: str = getenv("ILE_IQW_VALKEY_INPUT_STREAM", "payload-normalizer-stream")
    ILE_IQW_VALKEY_CONSUMER_GROUP: str = getenv("ILE_IQW_VALKEY_CONSUMER_GROUP", "questdb-writer-consumer-group")
    ILE_IQW_VALKEY_CONSUMER_NAME: str = getenv("ILE_IQW_VALKEY_CONSUMER_NAME", "questdb-writer-consumer-1")

    ILE_IQW_VALKEY_MESSAGES_CNT_KEY: str = getenv("ILE_IQW_VALKEY_MESSAGES_CNT_KEY", "questdb-writer-messages")
    ILE_IQW_VALKEY_ROWS_CNT_KEY: str = getenv("ILE_IQW_VALKEY_ROWS_CNT_KEY", "questdb-writer-rows")

    ILE_IQW_VALKEY_STREAM_TRIM_INTERVAL_S: str = getenv("ILE_IQW_VALKEY_STREAM_TRIM_INTERVAL_S", "21600")  # 6h

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

    valkey_stream_trim_interval_s: float = float(Env.ILE_IQW_VALKEY_STREAM_TRIM_INTERVAL_S)

    valkey_loop_wait_s: float = float(Env.ILE_IQW_VALKEY_LOOP_WAIT_S)
    valkey_loop_busy_wait_s: float = float(Env.ILE_IQW_VALKEY_LOOP_BUSY_WAIT_S)
    valkey_unavailable_wait_s: float = float(Env.ILE_IQW_VALKEY_UNAVAILABLE_WAIT_S)

    questdb_address: tuple[str, int] = (Env.ILE_IQW_QUESTDB_HOST, int(Env.ILE_IQW_QUESTDB_PORT))
    questdb_ssl: bool = Env.ILE_IQW_QUESTDB_SSL.lower() in ("1", "true")
    questdb_username: str = Env.ILE_IQW_QUESTDB_USERNAME
    questdb_password: str = Env.ILE_IQW_QUESTDB_PASSWORD

    batch_size: int = int(Env.ILE_IQW_BATCH_SIZE)


def build_questdb_conf_string() -> str:
    """Build QuestDB ILP HTTP connection string with optional SSL and auth."""
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
    """Write QuestDB rows and flush. Raises IngressError on failure."""
    for row in rows:
        sender.row(
            table_name=row["table"],
            symbols=row["symbols"] or None,
            columns=row["columns"] or None,
            at=questdb.ingress.TimestampNanos(row["timestamp_ns"]),
        )

    sender.flush()


class _SenderManager:
    """Manages QuestDB sender lifecycle with lazy initialization."""

    def __init__(self, questdb_conf: str) -> None:
        self._questdb_conf = questdb_conf
        self._sender: questdb.ingress.Sender | None = None

    def get(self) -> questdb.ingress.Sender:
        """Get or create sender. Raises IngressError if connection fails."""
        if self._sender is None:
            ile_tools.log_diagnostic("questdb: establishing sender")
            self._sender = questdb.ingress.Sender.from_conf(self._questdb_conf)
            self._sender.establish()
            ile_tools.log_diagnostic("questdb: sender established")
        return self._sender

    def close(self) -> None:
        """Close sender if open."""
        if self._sender is not None:
            try:
                ile_tools.log_diagnostic("questdb: closing sender")
                self._sender.close(flush=False)
                ile_tools.log_diagnostic("questdb: sender closed")
            except Exception as e:
                ile_tools.print_exception(e, "questdb: sender close failed")
            self._sender = None


def _unpack_messages(
    messages: list[tuple[bytes, dict[bytes, bytes]]],
) -> tuple[list[QuestDBRow], list[bytes]]:
    """
    Unpack messages from Valkey stream into QuestDBRow list.

    Returns (questdb_rows, message_ids). Malformed messages are logged and skipped.
    """
    questdb_rows: list[QuestDBRow] = []
    message_ids: list[bytes] = []

    for message_id, message_data in messages:
        # Always add message_id to ack list - malformed messages should be skipped, not retried
        message_ids.append(message_id)
        try:
            data_packed = message_data[b"data"]
            row: QuestDBRow = msgpack.unpackb(data_packed, raw=False)
            questdb_rows.append(row)
        except (KeyError, msgpack.exceptions.UnpackException) as e:
            ile_tools.print_exception(e, f"valkey: message unpack failed id={message_id!s}")

    return questdb_rows, message_ids


def _flatten_xreadgroup_messages(
    messages: dict[bytes, list[list[tuple[bytes, dict[bytes, bytes]]]]],
) -> list[tuple[bytes, dict[bytes, bytes]]]:
    """Flatten nested xreadgroup response into flat list of (message_id, data) tuples."""
    result: list[tuple[bytes, dict[bytes, bytes]]] = []
    for stream_messages in messages.values():
        for message_batch in stream_messages:
            result.extend(message_batch)
    return result


def _write_and_ack(
    r: valkey.Valkey,
    sender_mgr: _SenderManager,
    questdb_rows: list[QuestDBRow],
    message_ids: list[bytes],
) -> int:
    """
    Write rows to QuestDB and acknowledge messages in Valkey.

    Returns number of rows written. Raises IngressError or ValkeyError on failure.
    """
    if questdb_rows:
        write_rows_to_questdb(sender_mgr.get(), questdb_rows)

    if message_ids:
        with r.pipeline() as pipe:
            pipe.xack(Config.valkey_input_stream, Config.valkey_input_consumer_group, *message_ids)
            pipe.incr(Config.valkey_messages_cnt_key, len(message_ids))
            pipe.incr(Config.valkey_rows_cnt_key, len(questdb_rows))
            pipe.execute()

    return len(questdb_rows)


def process_stream(r: valkey.Valkey, sigterm_event: threading.Event) -> None:
    """
    Process messages from input stream and write to QuestDB.

    First processes pending (unacknowledged) messages, then enters main loop.
    Sets sigterm_event on fatal errors.
    """
    total_messages = 0
    total_rows = 0

    questdb_conf = build_questdb_conf_string()
    ile_tools.log_diagnostic(f"questdb: connection_string={questdb_conf}")

    sender_mgr = _SenderManager(questdb_conf)

    # Phase 1: Process pending messages (previously delivered but not acknowledged)
    pending_messages = 0
    pending_rows = 0
    next_start_id = "0-0"

    ile_tools.log_diagnostic("valkey: processing pending messages via xautoclaim")

    while not sigterm_event.is_set():
        try:
            autoclaim_result = ile_tools.v_cast(
                r.xautoclaim(
                    name=Config.valkey_input_stream,
                    groupname=Config.valkey_input_consumer_group,
                    consumername=Config.valkey_input_consumer_name,
                    min_idle_time=0,
                    start_id=next_start_id,
                    count=Config.batch_size,
                )
            )
            if not autoclaim_result or not autoclaim_result[1]:
                break

            next_start_id = autoclaim_result[0]
            claimed_messages = autoclaim_result[1]
            questdb_rows, message_ids = _unpack_messages(claimed_messages)
            message_count = len(message_ids)
            pending_messages += message_count

            if not questdb_rows:
                # Messages unpacked but no valid rows - still need to acknowledge
                if message_ids:
                    _write_and_ack(r, sender_mgr, [], message_ids)
                if next_start_id == b"0-0":
                    break
                sigterm_event.wait(Config.valkey_loop_busy_wait_s)
                continue

            try:
                rows_written = _write_and_ack(r, sender_mgr, questdb_rows, message_ids)
                pending_rows += rows_written

                ile_tools.log_result(f"questdb: wrote pending messages={message_count} rows={rows_written}")

            except questdb.ingress.IngressError as e:
                ile_tools.print_exception(
                    e, f"questdb: pending write failed rows_count={len(questdb_rows)} rows={questdb_rows}"
                )
                sigterm_event.set()
                break

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, "valkey: xautoclaim failed")
            sigterm_event.set()
            break

        if next_start_id == b"0-0":
            break
        sigterm_event.wait(Config.valkey_loop_busy_wait_s)

    if pending_messages > 0:
        ile_tools.log_diagnostic(f"valkey: pending messages processed messages={pending_messages} rows={pending_rows}")
    else:
        ile_tools.log_diagnostic("valkey: no pending messages found")

    total_messages += pending_messages
    total_rows += pending_rows

    # Phase 2: Process new messages via xreadgroup
    ile_tools.log_diagnostic("valkey: entering main loop via xreadgroup")

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

                flat_messages = _flatten_xreadgroup_messages(messages)
                questdb_rows, message_ids = _unpack_messages(flat_messages)
                message_count = len(message_ids)
                total_messages += message_count

                if not questdb_rows:
                    # Messages unpacked but no valid rows - still need to acknowledge
                    if message_ids:
                        _write_and_ack(r, sender_mgr, [], message_ids)
                    sigterm_event.wait(Config.valkey_loop_wait_s)
                    continue

                try:
                    rows_written = _write_and_ack(r, sender_mgr, questdb_rows, message_ids)
                    total_rows += rows_written

                    ile_tools.log_result(f"questdb: wrote messages={message_count} rows={rows_written}")
                except questdb.ingress.IngressError as e:
                    ile_tools.print_exception(
                        e, f"questdb: write failed rows_count={len(questdb_rows)} rows={questdb_rows}"
                    )
                    sigterm_event.set()

                # Use shorter wait if batch was full (likely more messages waiting)
                if message_count >= Config.batch_size:
                    sigterm_event.wait(Config.valkey_loop_busy_wait_s)
                else:
                    sigterm_event.wait(Config.valkey_loop_wait_s)

            except (valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError) as e:
                ile_tools.print_exception(
                    e, f"valkey: connection error, retrying in {Config.valkey_unavailable_wait_s}s"
                )
                sigterm_event.wait(Config.valkey_unavailable_wait_s)

            except valkey.exceptions.ValkeyError as e:
                ile_tools.print_exception(e, "valkey: xreadgroup failed")
                sigterm_event.set()

    finally:
        sender_mgr.close()

    ile_tools.log_result(f"main: finished total_messages={total_messages} total_rows={total_rows}")


def main() -> int:
    """Entry point for QuestDB writer. Returns 0 always (handles errors internally)."""
    ile_tools.log_diagnostic("main: starting questdb_writer")
    ile_tools.print_vars(Config)

    sigterm_event = ile_tools.configure_sigterm_handler()

    while not sigterm_event.is_set():
        try:
            # noinspection PyTypeChecker
            with ile_tools.create_valkey_client() as r:
                if not ile_tools.ensure_consumer_group(
                    r, Config.valkey_input_stream, Config.valkey_input_consumer_group
                ):
                    ile_tools.log_diagnostic(
                        f"main: consumer_group setup failed, retrying in {Config.valkey_unavailable_wait_s}s"
                    )
                    sigterm_event.wait(Config.valkey_unavailable_wait_s)
                    continue

                ile_tools.log_diagnostic("main: questdb_writer ready")

                # Start stream trim thread
                trim_thread = threading.Thread(
                    target=ile_tools.stream_trim_thread,
                    args=(
                        r,
                        sigterm_event,
                        Config.valkey_input_stream,
                        Config.batch_size,
                        Config.valkey_stream_trim_interval_s,
                    ),
                    daemon=True,
                    name="stream-trim-thread",
                )
                trim_thread.start()
                ile_tools.log_diagnostic("main: stream_trim_thread started")

                ile_tools.log_diagnostic("main: questdb_writer ready, waiting for messages")
                process_stream(r, sigterm_event)

        except (valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError) as e:
            ile_tools.print_exception(e, f"main: valkey unavailable, retrying in {Config.valkey_unavailable_wait_s}s")
            sigterm_event.wait(Config.valkey_unavailable_wait_s)

        except valkey.exceptions.ValkeyError as e:
            ile_tools.print_exception(e, "main: fatal valkey error")
            sigterm_event.set()

    ile_tools.log_diagnostic("main: questdb_writer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
