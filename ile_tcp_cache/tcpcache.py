#!venv/bin/python3
# https://github.com/python/typeshed/issues/7597#issuecomment-1117572695
from __future__ import annotations

import collections
import contextlib
import datetime
import enum
import functools
import os
import random
import re
import socket
import socketserver
import ssl
import sys
import threading
import time
import traceback
import typing

import requests

if typing.TYPE_CHECKING:
    from collections.abc import Callable

import redis

import ile_shared_tools

# ile-tcp-cache is useful when the target TCP server is unreachable from the data-producing device.

# Use case example: telegraf on a laptop with access to the database (target TCP server) once a day.
#                   Redis, collector, and delivery man components run on the laptop.

# Use case example: smart-home device is on a different network than a database (target TCP server).
#                   The smart home device writes the measurements to a cloud virtual machine.
#                   Redis and the collector components run in the cloud.
#                   The delivery man component runs on the database host computer.

#      [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Redis stream]
#              -> [Delivery man] -> [Destination location: target TCP]


def duration(value: str) -> datetime.timedelta:
    """Convert values like 1m30s100ms or 3s to datetime.timedelta."""
    pattern = r"^((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?((?P<milliseconds>\d+)ms)?$"
    match = re.match(pattern, value)
    if not match:
        msg = f"Invalid duration: {value}"
        raise ValueError(msg)
    groupdict = match.groupdict()
    minutes = int(groupdict["minutes"] or 0)
    seconds = int(groupdict["seconds"] or 0)
    milliseconds = int(groupdict["milliseconds"] or 0)
    return datetime.timedelta(minutes=minutes, seconds=seconds, milliseconds=milliseconds)


# https://stackoverflow.com/a/1094933/8574922
def size_fmt(num: float, mode: typing.Literal["metric", "binary"] = "metric", suffix: str = "") -> str:
    """Human friendly sizes, convert numbers to strings like 12.3K, 1.2Gi, 2.3GiB."""
    base = 1024.0 if mode == "binary" else 1000.0
    i = "i" if mode == "binary" and num >= base else ""
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < base:
            return f"{num:3.1f}{unit}{i}{suffix}"
        num /= base
    return f"{num:.1f}Y{i}{suffix}"


getenv = os.environ.get


# ITC = ile tcp cache
class Env:
    """Configuration, environment variables."""

    ILE_ITC_SOCKET_CONNECT_TIMEOUT: str = getenv("ILE_ITC_SOCKET_CONNECT_TIMEOUT", "5s")
    ILE_ITC_SOCKET_TIMEOUT: str = getenv("ILE_ITC_SOCKET_TIMEOUT", "30s")

    ILE_ITC_MY_TCP_BIND_HOST: str = getenv("ILE_ITC_MY_TCP_BIND_HOST", "127.0.0.1")
    ILE_ITC_MY_TCP_BIND_PORT: str = getenv("ILE_ITC_MY_TCP_BIND_PORT", "9009")
    ILE_ITC_MY_TCP_SSL: str = getenv("ILE_ITC_MY_TCP_SSL", "false")
    ILE_ITC_MY_TCP_SSL_CERTFILE: str = getenv("ILE_ITC_MY_TCP_SSL_CERTFILE", "server.pem")
    ILE_ITC_MY_TCP_SSL_KEYFILE: str = getenv("ILE_ITC_MY_TCP_SSL_KEYFILE", "server.crt")
    ILE_ITC_MY_TCP_SSL_PASSWORD: str = getenv("ILE_ITC_MY_TCP_SSL_PASSWORD", "")

    ILE_ITC_TARGET_TCP_HOST: str = getenv("ILE_ITC_TARGET_TCP_HOST", "127.0.0.1")
    ILE_ITC_TARGET_TCP_PORT: str = getenv("ILE_ITC_TARGET_TCP_PORT", "9009")
    ILE_ITC_TARGET_TCP_SSL: str = getenv("ILE_ITC_TARGET_TCP_SSL", "false")
    ILE_ITC_TARGET_TCP_SSL_CAFILE: str = getenv("ILE_ITC_TARGET_TCP_SSL_CAFILE", "")
    ILE_ITC_TARGET_TCP_SSL_CHECKHOSTNAME: str = getenv("ILE_ITC_TARGET_TCP_SSL_CHECKHOSTNAME", "true")
    ILE_ITC_TARGET_TCP_HEALTH_HTTP_URLS: str = getenv(
        "ILE_ITC_TARGET_TCP_HEALTH_HTTP_URLS", "http://localhost:9000/ping"
    )

    ILE_ITC_REDIS_HOST: str = getenv("ILE_ITC_REDIS_HOST", "127.0.0.1")
    ILE_ITC_REDIS_PORT: str = getenv("ILE_ITC_REDIS_PORT", "6379")
    ILE_ITC_REDIS_DB: str = getenv("ILE_ITC_REDIS_DB", "0")
    ILE_ITC_REDIS_PASSWORD: str = getenv("ILE_ITC_REDIS_PASSWORD", "")
    ILE_ITC_REDIS_SSL: str = getenv("ILE_ITC_REDIS_SSL", "false")
    ILE_ITC_REDIS_SSL_CAFILE: str = getenv("ILE_ITC_REDIS_SSL_CAFILE", "")
    ILE_ITC_REDIS_SSL_CHECKHOSTNAME: str = getenv("ILE_ITC_REDIS_SSL_CHECKHOSTNAME", "true")

    ILE_ITC_REDIS_STARTUP_TIMEOUT: str = getenv("ILE_ITC_REDIS_STARTUP_TIMEOUT", "30s")
    ILE_ITC_REDIS_STARTUP_RETRY_INTERVAL: str = getenv("ILE_ITC_REDIS_STARTUP_RETRY_INTERVAL", "1s")

    ILE_ITC_REDIS_STREAM_NAME: str = getenv("ILE_ITC_REDIS_STREAM_NAME", "itc")
    ILE_ITC_REDIS_STREAM_GROUP_NAME: str = getenv("ILE_ITC_REDIS_STREAM_GROUP_NAME", "itc")
    ILE_ITC_REDIS_STREAM_CONSUMER_NAME: str = getenv("ILE_ITC_REDIS_STREAM_CONSUMER_NAME", "itc")

    ILE_ITC_PARCEL_COLLECTOR_ENABLED: str = getenv("ILE_ITC_PARCEL_COLLECTOR_ENABLED", "true")
    ILE_ITC_PARCEL_COLLECTOR_CAPACITY: str = getenv("ILE_ITC_PARCEL_COLLECTOR_CAPACITY", "1024")
    ILE_ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL: str = getenv("ILE_ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL", "60s")

    ILE_ITC_REDIS_STREAM_READ_COUNT: str = getenv("ILE_ITC_REDIS_STREAM_READ_COUNT", "1024")
    ILE_ITC_DELIVERY_MAN_CAPACITY: str = getenv("ILE_ITC_DELIVERY_MAN_CAPACITY", "1024")

    ILE_ITC_DELIVERY_MAN_ENABLED: str = getenv("ILE_ITC_DELIVERY_MAN_ENABLED", "true")
    ILE_ITC_DELIVERY_MAN_COLLECT_INTERVAL: str = getenv("ILE_ITC_DELIVERY_MAN_COLLECT_INTERVAL", "60s")
    ILE_ITC_DELIVERY_MAN_FLUSH_INTERVAL: str = getenv("ILE_ITC_DELIVERY_MAN_FLUSH_INTERVAL", "60s")

    ILE_ITC_DELIVERY_MAN_THROTTLER: str = getenv("ILE_ITC_DELIVERY_MAN_THROTTLER", "1024/7s")

    ILE_ITC_COUNTER_HITS_ON_TARGET: str = getenv("ILE_ITC_COUNTER_HITS_ON_TARGET", "itc:hitsontarget")
    ILE_ITC_COUNTER_DELIVERED_MSGS: str = getenv("ILE_ITC_COUNTER_DELIVERED_MSGS", "itc:deliveredmsgs")
    ILE_ITC_COUNTER_DELIVERED_BYTES: str = getenv("ILE_ITC_COUNTER_DELIVERED_BYTES", "itc:deliveredbytes")
    ILE_ITC_COUNTER_DROPPED_MSGS: str = getenv("ILE_ITC_COUNTER_DROPPED_MSGS", "itc:droppedmsgs")
    ILE_ITC_COUNTER_DROPPED_BYTES: str = getenv("ILE_ITC_COUNTER_DROPPED_BYTES", "itc:droppedbytes")

    ILE_ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED: str = getenv(
        "ILE_ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED", "itc:pcoverloaded"
    )
    ILE_ITC_COUNTER_DELIVERY_MAN_OVERLOADED: str = getenv("ILE_ITC_COUNTER_DELIVERY_MAN_OVERLOADED", "itc:dmoverloaded")

    ILE_ITC_STATUS_INTERVAL: str = getenv("ILE_ITC_STATUS_INTERVAL", "60s")

    ILE_ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS: str = getenv(
        "ILE_ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS", "1.1,1.5,2.0,5.0"
    )
    ILE_ITC_PERIODIC_JITTER_MULTIPLIER: str = getenv("ILE_ITC_PERIODIC_JITTER_MULTIPLIER", "0.05")
    ILE_ITC_PERIODIC_DECELERATOR_DELAY: str = getenv("ILE_ITC_PERIODIC_DECELERATOR_DELAY", "500ms")


class Config:
    """Configuration, parsed environment variables."""

    socket_connect_timeout: datetime.timedelta = duration(Env.ILE_ITC_SOCKET_CONNECT_TIMEOUT)
    socket_timeout: datetime.timedelta = duration(Env.ILE_ITC_SOCKET_TIMEOUT)

    my_tcp_bind_address: tuple[str, int] = (Env.ILE_ITC_MY_TCP_BIND_HOST, int(Env.ILE_ITC_MY_TCP_BIND_PORT))
    my_tcp_ssl: bool = Env.ILE_ITC_MY_TCP_SSL.lower() == "true"
    my_tcp_ssl_certfile: str = Env.ILE_ITC_MY_TCP_SSL_CERTFILE
    my_tcp_ssl_keyfile: str = Env.ILE_ITC_MY_TCP_SSL_KEYFILE
    my_tcp_ssl_password: str | None = Env.ILE_ITC_MY_TCP_SSL_PASSWORD or None

    target_tcp_address: tuple[str, int] = (Env.ILE_ITC_TARGET_TCP_HOST, int(Env.ILE_ITC_TARGET_TCP_PORT))
    target_tcp_ssl: bool = Env.ILE_ITC_TARGET_TCP_SSL.lower() == "true"
    target_tcp_ssl_cafile: str | None = Env.ILE_ITC_TARGET_TCP_SSL_CAFILE or None
    target_tcp_ssl_checkhostname: bool = Env.ILE_ITC_TARGET_TCP_SSL_CHECKHOSTNAME.lower() == "true"
    target_tcp_health_http_urls: typing.Sequence[str] = list(
        map(str.strip, filter(None, Env.ILE_ITC_TARGET_TCP_HEALTH_HTTP_URLS.split(",")))
    )

    redis_address: tuple[str, int] = (Env.ILE_ITC_REDIS_HOST, int(Env.ILE_ITC_REDIS_PORT))
    redis_db: int = int(Env.ILE_ITC_REDIS_DB)
    redis_password: str | None = Env.ILE_ITC_REDIS_PASSWORD
    redis_ssl: bool = Env.ILE_ITC_REDIS_SSL.lower() == "true"
    redis_ssl_cafile: str | None = Env.ILE_ITC_REDIS_SSL_CAFILE or None
    redis_ssl_checkhostname: bool = Env.ILE_ITC_REDIS_SSL_CHECKHOSTNAME.lower() == "true"

    redis_startup_timeout: datetime.timedelta = duration(Env.ILE_ITC_REDIS_STARTUP_TIMEOUT)
    redis_startup_retry_interval: datetime.timedelta = duration(Env.ILE_ITC_REDIS_STARTUP_RETRY_INTERVAL)

    redis_stream_name: str = Env.ILE_ITC_REDIS_STREAM_NAME
    redis_stream_group_name: str = Env.ILE_ITC_REDIS_STREAM_GROUP_NAME
    redis_stream_consumer_name: str = Env.ILE_ITC_REDIS_STREAM_CONSUMER_NAME

    parcel_collector_enabled: bool = Env.ILE_ITC_PARCEL_COLLECTOR_ENABLED.lower() == "true"
    parcel_collector_capacity: int = int(Env.ILE_ITC_PARCEL_COLLECTOR_CAPACITY)
    parcel_collector_flush_interval: datetime.timedelta = duration(Env.ILE_ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL)

    redis_stream_read_count: int = int(Env.ILE_ITC_REDIS_STREAM_READ_COUNT)
    delivery_man_capacity: int = int(Env.ILE_ITC_DELIVERY_MAN_CAPACITY)

    delivery_man_enabled: bool = Env.ILE_ITC_DELIVERY_MAN_ENABLED.lower() == "true"
    delivery_man_collect_interval: datetime.timedelta = duration(Env.ILE_ITC_DELIVERY_MAN_COLLECT_INTERVAL)
    delivery_man_flush_interval: datetime.timedelta = duration(Env.ILE_ITC_DELIVERY_MAN_FLUSH_INTERVAL)

    _throttler_parts = Env.ILE_ITC_DELIVERY_MAN_THROTTLER.split("/")
    delivery_man_throttler_weight_limit: int = int(_throttler_parts[0])
    delivery_man_throttler_time_window: datetime.timedelta = duration(_throttler_parts[1])

    counter_hits_on_target: str = Env.ILE_ITC_COUNTER_HITS_ON_TARGET
    counter_delivered_msgs: str = Env.ILE_ITC_COUNTER_DELIVERED_MSGS
    counter_delivered_bytes: str = Env.ILE_ITC_COUNTER_DELIVERED_BYTES
    counter_dropped_msgs: str = Env.ILE_ITC_COUNTER_DROPPED_MSGS
    counter_dropped_bytes: str = Env.ILE_ITC_COUNTER_DROPPED_BYTES

    counter_parcel_collector_overloaded: str = Env.ILE_ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED
    counter_delivery_man_overloaded: str = Env.ILE_ITC_COUNTER_DELIVERY_MAN_OVERLOADED

    status_interval: datetime.timedelta = duration(Env.ILE_ITC_STATUS_INTERVAL)

    periodic_failure_backoff_multipliers: typing.Sequence[float] = list(
        map(float, filter(None, Env.ILE_ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS.split(",")))
    )
    periodic_jitter_multiplier: float = float(Env.ILE_ITC_PERIODIC_JITTER_MULTIPLIER)
    periodic_decelerator_delay: datetime.timedelta = duration(Env.ILE_ITC_PERIODIC_DECELERATOR_DELAY)


class VerboseThread(threading.Thread):
    """Thread with exception handling."""

    def run(self) -> None:
        try:
            super().run()
        except Exception as e:
            ile_shared_tools.print_exception(e)
            traceback.print_tb(e.__traceback__)
            raise


class Throttler:
    """Throttle with a weight limit within a given sliding time window."""

    class Permit:
        def __init__(self, p_time: float, p_weight: int) -> None:
            super().__init__()
            self.p_time = p_time
            self.p_weight = p_weight

    def __init__(self, weight_limit: int, time_window: datetime.timedelta) -> None:
        super().__init__()
        self.weight_limit = weight_limit
        self.time_window = time_window
        self.issued_permits: collections.deque[Throttler.Permit] = collections.deque()
        self.rlock = threading.RLock()

    def get_permit(self, requested_weight: int) -> Throttler.Permit:
        """Get a permit for the requested weight.

        :return: the permit, allowed weight may be 0 if the request was fully throttled.
        """
        with self.rlock:
            current_time = time.time()

            expired_time = current_time - self.time_window.total_seconds()
            while self.issued_permits and self.issued_permits[0].p_time < expired_time:
                self.issued_permits.popleft()

            total_weight = sum(permit.p_weight for permit in self.issued_permits)
            permit_weight = max(min(requested_weight, self.weight_limit - total_weight), 0)

            result_permit = Throttler.Permit(current_time, permit_weight)
            if permit_weight > 0:
                self.issued_permits.append(result_permit)

            return result_permit

    def next_at(self) -> float:
        """Get next non-throttled permit time.

        Retrieves the time when the next permit will be available.
        This method should be called only after get_permit() when the permit request was throttled.

        :return: timestamp indicating when the next permit will be available.
        """
        with self.rlock:
            if self.issued_permits:
                return self.issued_permits[0].p_time + self.time_window.total_seconds()
            return time.time()

    def cancel(self, permit: Throttler.Permit) -> None:
        """Cancel permit, mark as unused; ignore if already expired."""
        with self.rlock, contextlib.suppress(ValueError):
            self.issued_permits.remove(permit)


class VerboseInaccurateTimer(threading.Timer):
    """Timer with exception handling and untuned clock."""

    def __init__(
        self,
        interval: float,
        function: Callable[..., typing.Any],
        args: tuple[typing.Any] | None = None,
        kwargs: dict[typing.Any, typing.Any] | None = None,
    ) -> None:
        pjm = random.uniform(-Config.periodic_jitter_multiplier, Config.periodic_jitter_multiplier)  # noqa: S311
        super().__init__(interval * (1.0 + pjm), function, args, kwargs)

    def run(self) -> None:
        try:
            super().run()
        except Exception as e:
            ile_shared_tools.print_exception(e)
            traceback.print_tb(e.__traceback__)
            raise


class Periodic:
    """The task that will be executed periodically with a fixed delay."""

    class PeriodicResult(enum.Enum):
        """Result of the task execution."""

        REPEAT_ON_SCHEDULE = enum.auto()  # use the default interval
        REPEAT_WITH_BACKOFF = enum.auto()  # backoff is configurable using Config.periodic_failure_backoff_multipliers
        REPEAT_IMMEDIATELY = enum.auto()  # just repeat immediately
        REPEAT_THROTTLED = enum.auto()  # repeat almost immediately, take throttler into account

    def __init__(
        self,
        func: Callable[[], PeriodicResult],
        interval: datetime.timedelta,
        sigterm_threading_event: threading.Event,
        throttler: Throttler | None = None,
    ) -> None:
        super().__init__()
        self.func = func
        self.interval = interval
        self.sigterm_threading_event = sigterm_threading_event
        self.throttler = throttler
        self.backoff_idx = -1

    def start(self) -> None:
        VerboseThread(target=self._run, daemon=False).start()

    def _run(self) -> None:
        try:
            result = self.func()
        except Exception as e:
            ile_shared_tools.print_exception(e)
            traceback.print_tb(e.__traceback__)
            result = Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if self.sigterm_threading_event.is_set():
            return

        delay: float = 0.0
        match result:
            case Periodic.PeriodicResult.REPEAT_ON_SCHEDULE:
                self.backoff_idx = -1
                delay = self.interval.total_seconds()
            case Periodic.PeriodicResult.REPEAT_WITH_BACKOFF:
                self.backoff_idx = min(self.backoff_idx + 1, len(Config.periodic_failure_backoff_multipliers) - 1)
                delay = Config.periodic_failure_backoff_multipliers[self.backoff_idx] * self.interval.total_seconds()
            case Periodic.PeriodicResult.REPEAT_IMMEDIATELY:
                self.backoff_idx = -1
                delay = Config.periodic_decelerator_delay.total_seconds()
            case Periodic.PeriodicResult.REPEAT_THROTTLED:
                if not self.throttler:
                    msg = "Throttler is not set, but the task requested throttling."
                    raise ValueError(msg)
                self.backoff_idx = -1
                delay = max(
                    self.throttler.next_at() - time.time(),
                    Config.periodic_decelerator_delay.total_seconds(),
                )
            case _:
                msg = f"Unknown PeriodicResult: {result}"
                raise NotImplementedError(msg)

        if not self.sigterm_threading_event.is_set():
            timer = VerboseInaccurateTimer(delay, self._run)
            timer.daemon = False
            timer.start()


class TCPParcelCollector:
    """Collects parcels from TCP and delivers them to the warehouse (Redis stream).

    Handle processing: [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Redis stream].
    """

    def __init__(self, r: redis.Redis[str], sigterm_threading_event: threading.Event) -> None:
        super().__init__()
        self.r = r
        self.sigterm_threading_event = sigterm_threading_event
        self.backpack: collections.deque[str] = collections.deque(maxlen=Config.parcel_collector_capacity)
        self.handler_delivery_thread: threading.Thread | None = None

    class Handler(socketserver.StreamRequestHandler):
        """Handle processing: [Pickup location: my TCP] -> [Parcel collector]."""

        def __init__(
            self,
            request: socket.socket,
            client_address: tuple[str, int],
            server: socketserver.ThreadingTCPServer,
            outer: TCPParcelCollector,
        ) -> None:
            self.outer = outer
            super().__init__(request, client_address, server)

        def handle(self) -> None:
            try:
                while True:
                    data_bytes = self.rfile.readline()
                    if not data_bytes:
                        break

                    data = data_bytes.decode("utf-8").strip()
                    if not data:
                        break

                    self.outer.backpack.append(data)

                if (
                    len(self.outer.backpack) >= Config.parcel_collector_capacity * 0.75
                    and not self.outer.sigterm_threading_event.is_set()
                ):
                    VerboseThread(target=self._incr_overloaded_cnt, daemon=False).start()

                    # simple check to limit the number of threads
                    # synchronization not needed, more than one thread is allowed
                    if not self.outer.handler_delivery_thread or not self.outer.handler_delivery_thread.is_alive():
                        flush_thread = VerboseThread(target=self.outer.deliver_to_warehouse, daemon=False)
                        flush_thread.start()
                        self.outer.handler_delivery_thread = flush_thread

            except Exception as e:
                ile_shared_tools.print_exception(e)

        def _incr_overloaded_cnt(self) -> None:
            try:
                self.outer.r.incr(Config.counter_parcel_collector_overloaded, 1)
            except redis.exceptions.RedisError as e:
                ile_shared_tools.print_exception(e)

    def handler_factory(
        self, request: socket.socket, client_address: tuple[str, int], server: socketserver.ThreadingTCPServer
    ) -> TCPParcelCollector.Handler:
        return TCPParcelCollector.Handler(request, client_address, server, self)

    def deliver_to_warehouse(self) -> Periodic.PeriodicResult:
        """Handle processing: [Parcel collector] -> [Warehouse: Redis stream]."""
        data = None
        try:
            flushed = 0
            while self.backpack and flushed < Config.parcel_collector_capacity * 2:
                try:
                    data = self.backpack.popleft()
                except IndexError:
                    break

                self.r.xadd(name=Config.redis_stream_name, id="*", fields={"data": data})
                flushed += 1

        except redis.exceptions.RedisError as e:
            ile_shared_tools.print_exception(e)
            if data:
                self.backpack.appendleft(data)
            return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        else:
            return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE


class DeliveryMan:
    """Collects parcels from the warehouse (Redis stream) and delivers them to the target TCP.

    Handle processing: [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP].
    """

    class Message:
        def __init__(self, rstream_entry: tuple[str, dict[str, str]]) -> None:
            super().__init__()
            self.message_id: str = rstream_entry[0]
            self.data: bytes = rstream_entry[1]["data"].encode("utf-8")

    class DeliveryResult(enum.Enum):
        OK_DELIVERED = enum.auto()
        OK_DROPPED = enum.auto()
        OK_THROTTLED = enum.auto()
        FAIL_EXCEPTION = enum.auto()

    def __init__(self, r: redis.Redis[str], throttler: Throttler) -> None:
        super().__init__()
        self.r = r
        self.throttler = throttler
        self.backpack: list[DeliveryMan.Message] = []
        self.backpack_rlock = threading.RLock()
        self.last_flush = time.time()

    def collect_from_warehouse_and_occasionally_deliver_to_target_tcp(self) -> Periodic.PeriodicResult:
        """Handle processing: [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP].

        Does the [Warehouse: Redis stream] -> [Delivery man] processing.
        Also, occasionally calls handling [Delivery man] -> [Destination location: target TCP].
        """
        xread = min(max(Config.delivery_man_capacity - len(self.backpack), 1), Config.redis_stream_read_count)
        redis_exception = None
        try:
            messages = self._collect_from_warehouse(xread)
        except redis.exceptions.RedisError as e:
            ile_shared_tools.print_exception(e)
            # Failed to collect new messages.
            # However, there might be something in the backpack.
            # Further processing is needed. Store the exception and continue.
            redis_exception = e
            messages = []

        with self.backpack_rlock:
            self.backpack.extend(messages)

        if len(self.backpack) >= Config.delivery_man_capacity:
            self._incr_overloaded_cnt()

        if len(self.backpack) >= Config.delivery_man_capacity or (
            (time.time() - self.last_flush) >= Config.delivery_man_flush_interval.total_seconds()
        ):
            delivery_result = self._deliver_to_target_tcp()

            match delivery_result:
                case DeliveryMan.DeliveryResult.FAIL_EXCEPTION | DeliveryMan.DeliveryResult.OK_DROPPED:
                    return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

                case DeliveryMan.DeliveryResult.OK_THROTTLED:
                    return Periodic.PeriodicResult.REPEAT_THROTTLED

                case DeliveryMan.DeliveryResult.OK_DELIVERED:
                    pass  # nice, continue

                case _:
                    msg = f"Unknown DeliveryResult: {delivery_result}"
                    raise NotImplementedError(msg)

        if redis_exception is not None:
            return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if len(messages) >= xread:
            return Periodic.PeriodicResult.REPEAT_IMMEDIATELY

        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE

    def _collect_from_warehouse(self, count: int) -> list[DeliveryMan.Message]:
        """Collect messages from the warehouse (Redis stream).

        :param count: maximum number of messages to collect.
        :return: list of DeliveryMan.Message.
        :raises: redis.exceptions.RedisError.
        """
        if count == 0:
            return []

        # xreadgroup = [(stream_name, [(message_id, {field: value}), ...]), ...]
        xreadgroup = self.r.xreadgroup(
            groupname=Config.redis_stream_group_name,
            consumername=Config.redis_stream_consumer_name,
            streams={Config.redis_stream_name: ">"},
            count=count,
            noack=False,
        )

        if len(xreadgroup) > 0:
            data = xreadgroup[0][1]
            data = filter(lambda entry: bool(entry[1]), data)  # skip marked as deleted (entry[1] == {})
            data = filter(lambda entry: "data" in entry[1], data)  # skip invalid entries
            messages = [DeliveryMan.Message(entry) for entry in data]
        else:
            messages = []

        return messages

    @staticmethod
    def _health_check_target_tcp() -> bool:
        """Check if the target TCP is healthy."""
        if not Config.target_tcp_health_http_urls:
            return True
        try:
            timeout = (Config.socket_connect_timeout.total_seconds(), Config.socket_timeout.total_seconds())
            verify = Config.target_tcp_ssl_cafile if Config.target_tcp_ssl_checkhostname else False
            with requests.session() as session:
                for health_url in Config.target_tcp_health_http_urls:
                    with session.get(health_url, timeout=timeout, verify=verify) as response:
                        ile_shared_tools.print_debug(
                            functools.partial(
                                lambda h_url: f"target_tcp_health: {h_url} -> {response.status_code}", h_url=health_url
                            )
                        )
                        response.raise_for_status()
        except requests.exceptions.RequestException as e:
            ile_shared_tools.print_exception(e)
            return False
        else:
            return True

    def _deliver_to_target_tcp(self) -> DeliveryResult:
        """Handle [Delivery man] -> [Destination location: target TCP]."""
        if len(self.backpack) == 0:
            return DeliveryMan.DeliveryResult.OK_DELIVERED

        throttler_permit = self.throttler.get_permit(len(self.backpack))
        if throttler_permit.p_weight == 0:
            return DeliveryMan.DeliveryResult.OK_THROTTLED

        if Config.target_tcp_ssl:
            ssl_context = ssl.create_default_context(
                purpose=ssl.Purpose.SERVER_AUTH, cafile=Config.target_tcp_ssl_cafile
            )
            ssl_context.check_hostname = Config.target_tcp_ssl_checkhostname
        else:
            ssl_context = None

        with (
            socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock0,
            (
                ssl_context.wrap_socket(sock0, server_hostname=Config.target_tcp_address[0]) if ssl_context else sock0
            ) as sock,
        ):
            # Synchronize access to the backpack:
            #   collect bytes, connect to the target TCP, empty the backpack.
            # If these operations are successful, the data will be processed.
            # If any of these operations fail, end the method by delivery fail.

            if not DeliveryMan._health_check_target_tcp():
                # Healthcheck error is not a 'final problem', it will be retried later.
                # Return false as the data was not processed.
                self.throttler.cancel(throttler_permit)
                return DeliveryMan.DeliveryResult.FAIL_EXCEPTION

            with self.backpack_rlock:
                if len(self.backpack) == 0:
                    return DeliveryMan.DeliveryResult.OK_DELIVERED

                try:
                    sock.settimeout(Config.socket_connect_timeout.total_seconds())
                    sock.connect(Config.target_tcp_address)
                except OSError as e:
                    # Connect error is not a 'final problem', it will be retried later.
                    # Return false as the data was not processed.
                    ile_shared_tools.print_exception(e)
                    self.throttler.cancel(throttler_permit)
                    return DeliveryMan.DeliveryResult.FAIL_EXCEPTION

                backpack_throttled = self.backpack[: throttler_permit.p_weight]

                data = b"\n".join(message.data for message in backpack_throttled) + b"\n"
                message_ids = [message.message_id for message in backpack_throttled]

                self.last_flush = time.time()
                self.backpack = self.backpack[throttler_permit.p_weight :]

            # Socket is connected, deliver the data or drop it.
            try:
                sock.settimeout(Config.socket_timeout.total_seconds())
                sock.sendall(data)

                # Send one more empty line after a while.
                # Make sure that the server did not close the connection
                # (questdb will do that asynchronously if the data was incorrect).
                # https://github.com/questdb/questdb/blob/8.2.1/core/src/main/java/io/questdb/network/AbstractIODispatcher.java#L159
                time.sleep(ile_shared_tools.QUESTDB_EXTRA_ASYNC_SLEEP)
                sock.sendall(b"\n")

                sock.shutdown(socket.SHUT_RDWR)
                sock.close()

                delivered = True
            except OSError as e:
                # Error while sending data is a 'final problem', it will not be retried.
                # Data may be corrupted, so it is better to drop it.
                # Print exception, mark data as not delivered and continue.
                ile_shared_tools.print_exception(e)
                delivered = False

        # At this point data was processed - delivered or dropped.
        try:
            self.r.xack(Config.redis_stream_name, Config.redis_stream_group_name, *message_ids)
            self.r.xdel(Config.redis_stream_name, *message_ids)
            self.r.incr(Config.counter_hits_on_target, 1)

            if delivered:
                self.r.incr(Config.counter_delivered_msgs, len(message_ids))
                self.r.incr(Config.counter_delivered_bytes, len(data))
            else:
                self.r.incr(Config.counter_dropped_msgs, len(message_ids))
                self.r.incr(Config.counter_dropped_bytes, len(data))

        except redis.exceptions.RedisError as e:
            ile_shared_tools.print_exception(e)

        # Return true as the data was processed.
        return DeliveryMan.DeliveryResult.OK_DELIVERED if delivered else DeliveryMan.DeliveryResult.OK_DROPPED

    def _incr_overloaded_cnt(self) -> None:
        try:
            self.r.incr(Config.counter_delivery_man_overloaded, 1)
        except redis.exceptions.RedisError as e:
            ile_shared_tools.print_exception(e)


def status(
    parcel_collector: TCPParcelCollector | None, delivery_man: DeliveryMan | None, r: redis.Redis[str]
) -> Periodic.PeriodicResult:
    try:
        threading_active_count = threading.active_count()

        parcel_collector_backpack = len(parcel_collector.backpack) if parcel_collector else -1
        delivery_man_backpack = len(delivery_man.backpack) if delivery_man else -1

        warehouse_xlen = r.xlen(name=Config.redis_stream_name)
        warehouse_xpending = r.xpending(name=Config.redis_stream_name, groupname=Config.redis_stream_group_name)[
            "pending"
        ]

        counter_hits_on_target = size_fmt(int(r.get(Config.counter_hits_on_target) or -1))
        counter_delivered_msgs = size_fmt(int(r.get(Config.counter_delivered_msgs) or -1))
        counter_delivered_bytes = size_fmt(int(r.get(Config.counter_delivered_bytes) or -1), mode="binary", suffix="B")
        counter_dropped_msgs = size_fmt(int(r.get(Config.counter_dropped_msgs) or -1))
        counter_dropped_bytes = size_fmt(int(r.get(Config.counter_dropped_bytes) or -1), mode="binary", suffix="B")

        counter_parcel_collector_overloaded = size_fmt(int(r.get(Config.counter_parcel_collector_overloaded) or -1))
        counter_delivery_man_overloaded = size_fmt(int(r.get(Config.counter_delivery_man_overloaded) or -1))

        ile_shared_tools.print_(
            f"threading.active_count: {threading_active_count} | "
            f"parcel_collector.backpack: {parcel_collector_backpack} | "
            f"delivery_man.backpack: {delivery_man_backpack} | "
            f"warehouse.xlen: {warehouse_xlen} | "
            f"warehouse.xpending: {warehouse_xpending} | "
            f"counter.hits_on_target: {counter_hits_on_target} | "
            f"counter.delivered_msgs: {counter_delivered_msgs} | "
            f"counter.delivered_bytes: {counter_delivered_bytes} | "
            f"counter.dropped_msgs: {counter_dropped_msgs} | "
            f"counter.dropped_bytes: {counter_dropped_bytes} | "
            f"counter.parcel_collector_overloaded: {counter_parcel_collector_overloaded} | "
            f"counter.delivery_man_overloaded: {counter_delivery_man_overloaded}",
        )

    except redis.exceptions.RedisError as e:
        ile_shared_tools.print_exception(e)
        return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

    else:
        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE


def wait_for_redis(r: redis.Redis[str]) -> bool:
    must_end = time.time() + Config.redis_startup_timeout.total_seconds()

    while True:
        try:
            r.ping()

        except (redis.exceptions.ConnectionError, ConnectionError) as e:
            ile_shared_tools.print_exception(e)

            if time.time() > must_end:
                return False

            time.sleep(Config.redis_startup_retry_interval.total_seconds())

        else:
            return True


def redis_init(r: redis.Redis[str]) -> bool:
    try:
        init_id = r.xadd(name=Config.redis_stream_name, id="*", fields={"init": "1"})
        r.xdel(Config.redis_stream_name, init_id)

        xinfo_groups = r.xinfo_groups(name=Config.redis_stream_name)
        for xgroup in xinfo_groups:
            r.xgroup_destroy(name=Config.redis_stream_name, groupname=xgroup["name"])

        r.xgroup_create(name=Config.redis_stream_name, groupname=Config.redis_stream_group_name, id="0")

        r.incr(Config.counter_hits_on_target, 0)
        r.incr(Config.counter_delivered_msgs, 0)
        r.incr(Config.counter_delivered_bytes, 0)
        r.incr(Config.counter_dropped_msgs, 0)
        r.incr(Config.counter_dropped_bytes, 0)

        r.incr(Config.counter_parcel_collector_overloaded, 0)
        r.incr(Config.counter_delivery_man_overloaded, 0)

    except redis.exceptions.RedisError as e:
        ile_shared_tools.print_exception(e)
        return False

    else:
        return True


def main() -> int:
    ile_shared_tools.print_vars(Config)

    sigterm_threading_event = ile_shared_tools.configure_sigterm_handler()

    r = redis.Redis(
        host=Config.redis_address[0],
        port=Config.redis_address[1],
        ssl=Config.redis_ssl,
        ssl_ca_certs=Config.redis_ssl_cafile,
        ssl_check_hostname=Config.redis_ssl_checkhostname,
        db=Config.redis_db,
        password=Config.redis_password,
        socket_timeout=Config.socket_timeout.total_seconds(),
        socket_connect_timeout=Config.socket_connect_timeout.total_seconds(),
        decode_responses=True,
    )

    if not wait_for_redis(r):
        return -1

    if not redis_init(r):
        return -1

    if Config.parcel_collector_enabled:
        # [Pickup location: my TCP] -> [Parcel collector]
        tcp_parcel_collector = TCPParcelCollector(r, sigterm_threading_event)
        tcp_server = socketserver.ThreadingTCPServer(Config.my_tcp_bind_address, tcp_parcel_collector.handler_factory)
        if Config.my_tcp_ssl:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(
                Config.my_tcp_ssl_certfile, Config.my_tcp_ssl_keyfile, Config.my_tcp_ssl_password
            )
            tcp_server.socket = ssl_context.wrap_socket(tcp_server.socket, server_side=True)
        tcp_server_thread = VerboseThread(target=tcp_server.serve_forever, daemon=True)
        tcp_server_thread.start()

        # [Parcel collector] -> [Warehouse: Redis stream]
        parcel_collector_periodic_flush = Periodic(
            tcp_parcel_collector.deliver_to_warehouse,
            Config.parcel_collector_flush_interval,
            sigterm_threading_event,
        )
        parcel_collector_periodic_flush.start()
    else:
        tcp_parcel_collector = None

    if Config.delivery_man_enabled:
        # [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP]
        delivery_throttler = Throttler(
            Config.delivery_man_throttler_weight_limit, Config.delivery_man_throttler_time_window
        )
        delivery_man = DeliveryMan(r, delivery_throttler)
        delivery_man_periodic_collect = Periodic(
            delivery_man.collect_from_warehouse_and_occasionally_deliver_to_target_tcp,
            Config.delivery_man_collect_interval,
            sigterm_threading_event,
            delivery_throttler,
        )
        delivery_man_periodic_collect.start()
    else:
        delivery_man = None

    periodic_status = Periodic(
        lambda: status(tcp_parcel_collector, delivery_man, r),
        Config.status_interval,
        sigterm_threading_event,
    )
    periodic_status.start()

    sigterm_threading_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
