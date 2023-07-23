#!venv/bin/python3
import collections
import datetime
import enum
import os
import random
import re
import signal
import socket
import socketserver
import sys
import threading
import time
import traceback
import typing
from collections.abc import Callable

import redis

if typing.TYPE_CHECKING:
    import types


# ile-tcp-cache is useful when the target TCP server is not always available, and you don't want to lose data.
# Use case example: telegraf on a laptop that has accesses the questdb once a day.

#      [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Redis stream]
#              -> [Delivery man] -> [Destination location: target TCP]


def duration(value: str) -> datetime.timedelta:
    """Convert values like 1m30s100ms or 3s to datetime.timedelta."""
    match = re.match(r"((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?((?P<milliseconds>\d+)ms)?", value)
    if not match:
        msg = f"Invalid duration: {value}"
        raise ValueError(msg)
    matchdict = match.groupdict()
    return datetime.timedelta(
        minutes=int(matchdict["minutes"] or 0),
        seconds=int(matchdict["seconds"] or 0),
        milliseconds=int(matchdict["milliseconds"] or 0),
    )


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


class Env:
    """Configuration, environment variables."""

    ITC_SOCKET_CONNECT_TIMEOUT: str = getenv("ITC_SOCKET_CONNECT_TIMEOUT", "5s")
    ITC_SOCKET_TIMEOUT: str = getenv("ITC_SOCKET_TIMEOUT", "30s")

    ITC_MY_TCP_BIND_HOST: str = getenv("ITC_MY_TCP_BIND_HOST", "127.0.0.1")
    ITC_MY_TCP_BIND_PORT: str = getenv("ITC_MY_TCP_BIND_PORT", "9009")

    ITC_TARGET_TCP_HOST: str = getenv("ITC_TARGET_TCP_HOST", "127.0.0.1")
    ITC_TARGET_TCP_PORT: str = getenv("ITC_TARGET_TCP_PORT", "9009")

    ITC_REDIS_HOST: str = getenv("ITC_REDIS_HOST", "127.0.0.1")
    ITC_REDIS_PORT: str = getenv("ITC_REDIS_PORT", "6379")
    ITC_REDIS_DB: str = getenv("ITC_REDIS_DB", "0")
    ITC_REDIS_PASSWORD: str | None = getenv("ITC_REDIS_PASSWORD", None)

    ITC_REDIS_STARTUP_TIMEOUT: str = getenv("ITC_REDIS_STARTUP_TIMEOUT", "30s")
    ITC_REDIS_STARTUP_RETRY_INTERVAL: str = getenv("ITC_REDIS_STARTUP_RETRY_INTERVAL", "1s")

    ITC_REDIS_STREAM_NAME: str = getenv("ITC_REDIS_STREAM_NAME", "itc")
    ITC_REDIS_STREAM_GROUP_NAME: str = getenv("ITC_REDIS_STREAM_GROUP_NAME", "itc")
    ITC_REDIS_STREAM_CONSUMER_NAME: str = getenv("ITC_REDIS_STREAM_CONSUMER_NAME", "itc")

    ITC_PARCEL_COLLECTOR_CAPACITY: str = getenv("ITC_PARCEL_COLLECTOR_CAPACITY", "2048")
    ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL: str = getenv("ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL", "60s")

    ITC_REDIS_STREAM_READ_COUNT: str = getenv("ITC_REDIS_STREAM_READ_COUNT", "2048")
    ITC_DELIVERY_MAN_CAPACITY: str = getenv("ITC_DELIVERY_MAN_CAPACITY", "4096")

    ITC_DELIVERY_MAN_COLLECT_INTERVAL: str = getenv("ITC_DELIVERY_MAN_COLLECT_INTERVAL", "60s")
    ITC_DELIVERY_MAN_FLUSH_INTERVAL: str = getenv("ITC_DELIVERY_MAN_FLUSH_INTERVAL", "60s")

    ITC_COUNTER_HITS_ON_TARGET: str = getenv("ITC_COUNTER_HITS_ON_TARGET", "itc:hitsontarget")
    ITC_COUNTER_DELIVERED_MSGS: str = getenv("ITC_COUNTER_DELIVERED_MSGS", "itc:deliveredmsgs")
    ITC_COUNTER_DELIVERED_BYTES: str = getenv("ITC_COUNTER_DELIVERED_BYTES", "itc:deliveredbytes")
    ITC_COUNTER_DROPPED_MSGS: str = getenv("ITC_COUNTER_DROPPED_MSGS", "itc:droppedmsgs")
    ITC_COUNTER_DROPPED_BYTES: str = getenv("ITC_COUNTER_DROPPED_BYTES", "itc:droppedbytes")

    ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED: str = getenv("ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED", "itc:pcoverloaded")
    ITC_COUNTER_DELIVERY_MAN_OVERLOADED: str = getenv("ITC_COUNTER_DELIVERY_MAN_OVERLOADED", "itc:dmoverloaded")

    ITC_STATUS_INTERVAL: str = getenv("ITC_STATUS_INTERVAL", "60s")

    ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS: str = getenv(
        "ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS",
        "1.1,1.5,2.0,5.0",
    )
    ITC_PERIODIC_JITTER: str = getenv("ITC_PERIODIC_JITTER", "0.05")


class Config:
    """Configuration, parsed environment variables."""

    socket_connect_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_CONNECT_TIMEOUT)
    socket_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_TIMEOUT)

    my_tcp_bind_address: tuple[str, int] = (Env.ITC_MY_TCP_BIND_HOST, int(Env.ITC_MY_TCP_BIND_PORT))
    target_tcp_address: tuple[str, int] = (Env.ITC_TARGET_TCP_HOST, int(Env.ITC_TARGET_TCP_PORT))

    redis_address: tuple[str, int] = (Env.ITC_REDIS_HOST, int(Env.ITC_REDIS_PORT))
    redis_db: int = int(Env.ITC_REDIS_DB)
    redis_password: str | None = Env.ITC_REDIS_PASSWORD

    redis_startup_timeout: datetime.timedelta = duration(Env.ITC_REDIS_STARTUP_TIMEOUT)
    redis_startup_retry_interval: datetime.timedelta = duration(Env.ITC_REDIS_STARTUP_RETRY_INTERVAL)

    redis_stream_name: str = Env.ITC_REDIS_STREAM_NAME
    redis_stream_group_name: str = Env.ITC_REDIS_STREAM_GROUP_NAME
    redis_stream_consumer_name: str = Env.ITC_REDIS_STREAM_CONSUMER_NAME

    parcel_collector_capacity: int = int(Env.ITC_PARCEL_COLLECTOR_CAPACITY)
    parcel_collector_flush_interval: datetime.timedelta = duration(Env.ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL)

    redis_steam_read_count: int = int(Env.ITC_REDIS_STREAM_READ_COUNT)
    delivery_man_capacity: int = int(Env.ITC_DELIVERY_MAN_CAPACITY)

    delivery_man_collect_interval: datetime.timedelta = duration(Env.ITC_DELIVERY_MAN_COLLECT_INTERVAL)
    delivery_man_flush_interval: datetime.timedelta = duration(Env.ITC_DELIVERY_MAN_FLUSH_INTERVAL)

    counter_hits_on_target: str = Env.ITC_COUNTER_HITS_ON_TARGET
    counter_delivered_msgs: str = Env.ITC_COUNTER_DELIVERED_MSGS
    counter_delivered_bytes: str = Env.ITC_COUNTER_DELIVERED_BYTES
    counter_dropped_msgs: str = Env.ITC_COUNTER_DROPPED_MSGS
    counter_dropped_bytes: str = Env.ITC_COUNTER_DROPPED_BYTES

    counter_parcel_collector_overloaded: str = Env.ITC_COUNTER_PARCEL_COLLECTOR_OVERLOADED
    counter_delivery_man_overloaded: str = Env.ITC_COUNTER_DELIVERY_MAN_OVERLOADED

    status_interval: datetime.timedelta = duration(Env.ITC_STATUS_INTERVAL)

    periodic_failure_backoff_multipliers: typing.Sequence[float] = list(
        map(float, filter(None, Env.ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS.split(","))),
    )
    periodic_jitter: float = float(Env.ITC_PERIODIC_JITTER)


# noinspection DuplicatedCode
def print_(*args, **kwargs) -> None:
    """Print with timestamp."""
    timestamp = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    new_args = (timestamp, *args)
    print(*new_args, **kwargs)


# noinspection DuplicatedCode
def print_exception(exception: BaseException) -> None:
    """Print exception summary."""
    exc_traceback: types.TracebackType | None = exception.__traceback__
    if exc_traceback:
        co_filename = exc_traceback.tb_frame.f_code.co_filename
        tb_lineno = exc_traceback.tb_lineno
        co_name = exc_traceback.tb_frame.f_code.co_name
        format_exception_only = traceback.format_exception_only(type(exception), exception)[0].strip()
        print_(f"exception: {co_filename}:{tb_lineno} ({co_name}) {format_exception_only}", file=sys.stderr)
    else:
        print_(f"exception: {exception}", file=sys.stderr)


# noinspection DuplicatedCode
def configure_sigterm_handler() -> threading.Event:
    """Configure SIGTERM and SIGINT handler."""
    sigterm_cnt = [0]
    sigterm_threading_event = threading.Event()

    def sigterm_handler(signal_number: int, _current_stack_frame) -> None:
        signal_name = signal.Signals(signal_number).name

        sigterm_cnt[0] += 1
        if sigterm_cnt[0] == 1:
            print_(f"Program interrupted by the {signal_name}, graceful shutdown in progress.", file=sys.stderr)
            sigterm_threading_event.set()

            for thing in threading.enumerate():
                if isinstance(thing, threading.Timer):
                    print_(f"Canceling threading.Timer: {thing}")
                    thing.cancel()
        else:
            print_(f"Program interrupted by the {signal_name} again, forced shutdown in progress.", file=sys.stderr)
            sys.exit(-1)

    for some_signal in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(some_signal, sigterm_handler)

    return sigterm_threading_event


class VerboseThread(threading.Thread):
    """Thread with exception handling."""

    def run(self) -> None:
        try:
            super().run()
        except Exception as e:
            print_exception(e)
            traceback.print_tb(e.__traceback__)
            raise


class VerboseInaccurateTimer(threading.Timer):
    """Timer with exception handling and untuned clock."""

    def __init__(self, interval: float, function: Callable[..., typing.Any], args=None, kwargs=None) -> None:
        jitter = random.uniform(-Config.periodic_jitter, Config.periodic_jitter) * interval
        super().__init__(interval + jitter, function, args, kwargs)

    def run(self) -> None:
        try:
            super().run()
        except Exception as e:
            print_exception(e)
            traceback.print_tb(e.__traceback__)
            raise


class Periodic:
    """The task that will be executed periodically with a fixed delay."""

    class PeriodicResult(enum.Enum):
        """Result of the task execution."""

        REPEAT_ON_SCHEDULE = enum.auto()  # use the default interval
        REPEAT_WITH_BACKOFF = enum.auto()  # backoff is configurable using Config.periodic_failure_backoff_multipliers
        REPEAT_IMMEDIATELY = enum.auto()  # just repeat immediately

    def __init__(
        self,
        func: Callable[[], PeriodicResult],
        interval: datetime.timedelta,
        sigterm_threading_event: threading.Event,
    ) -> None:
        super().__init__()
        self.func = func
        self.interval = interval
        self.sigterm_threading_event = sigterm_threading_event
        self.backoff_idx = -1

    def start(self) -> None:
        VerboseThread(target=self._run, daemon=False).start()

    def _run(self) -> None:
        try:
            result = self.func()
        except Exception as e:
            print_exception(e)
            traceback.print_tb(e.__traceback__)
            result = Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if result == Periodic.PeriodicResult.REPEAT_ON_SCHEDULE:
            self.backoff_idx = -1
            delay = self.interval.total_seconds()
        elif result == Periodic.PeriodicResult.REPEAT_WITH_BACKOFF:
            self.backoff_idx = min(self.backoff_idx + 1, len(Config.periodic_failure_backoff_multipliers) - 1)
            delay = Config.periodic_failure_backoff_multipliers[self.backoff_idx] * self.interval.total_seconds()
        elif result == Periodic.PeriodicResult.REPEAT_IMMEDIATELY:
            self.backoff_idx = -1
            delay = 0
        else:
            raise NotImplementedError

        if not self.sigterm_threading_event.is_set():
            timer = VerboseInaccurateTimer(delay, self._run)
            timer.daemon = False
            timer.start()


class TCPParcelCollector:
    """Collects parcels from TCP and delivers them to the warehouse (Redis stream).

    Handle processing: [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Redis stream].
    """

    def __init__(self, r: redis.Redis, sigterm_threading_event: threading.Event) -> None:
        super().__init__()
        self.r = r
        self.sigterm_threading_event = sigterm_threading_event
        self.backpack: collections.deque[str] = collections.deque(maxlen=Config.parcel_collector_capacity)
        self.handler_delivery_thread: threading.Thread | None = None

    class Handler(socketserver.StreamRequestHandler):
        """Handle processing: [Pickup location: my TCP] -> [Parcel collector]."""

        def __init__(self, request, client_address, server, outer: "TCPParcelCollector") -> None:
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
                print_exception(e)

        def _incr_overloaded_cnt(self) -> None:
            try:
                self.outer.r.incr(Config.counter_parcel_collector_overloaded, 1)
            except redis.exceptions.RedisError as e:
                print_exception(e)

    def handler_factory(self, request, client_address, server) -> "TCPParcelCollector.Handler":
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
            print_exception(e)
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
        def __init__(self, rstream_entry: tuple[str, dict]) -> None:
            super().__init__()
            self.message_id: str = rstream_entry[0]
            self.data: bytes = rstream_entry[1]["data"].encode("utf-8")

    def __init__(self, r: redis.Redis) -> None:
        super().__init__()
        self.r = r
        self.backpack: list[DeliveryMan.Message] = []
        self.backpack_rlock = threading.RLock()
        self.last_flush = time.time()

    def collect_from_warehouse_and_occasionally_deliver_to_target_tcp(self) -> Periodic.PeriodicResult:
        """Handle processing: [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP].

        Does the [Warehouse: Redis stream] -> [Delivery man] processing..
        Also, occasionally calls handling [Delivery man] -> [Destination location: target TCP].
        """
        if len(self.backpack) >= Config.delivery_man_capacity:
            self._incr_overloaded_cnt()
            if not self._deliver_to_target_tcp():
                return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        xread = min(max(Config.delivery_man_capacity - len(self.backpack), 1), Config.redis_steam_read_count)
        redis_exception = None

        try:
            # xreadgroup = [(stream_name, [(message_id, {field: value}), ...]), ...]
            xreadgroup = self.r.xreadgroup(
                groupname=Config.redis_stream_group_name,
                consumername=Config.redis_stream_consumer_name,
                streams={Config.redis_stream_name: ">"},
                count=xread,
                noack=False,
            )

        except redis.exceptions.RedisError as e:
            print_exception(e)
            redis_exception = e
            xreadgroup = []

        if len(xreadgroup) > 0:
            data = xreadgroup[0][1]
            data = filter(lambda entry: bool(entry[1]), data)  # skip marked as deleted (entry[1] == {})
            data = filter(lambda entry: "data" in entry[1], data)  # skip invalid entries
            messages = [DeliveryMan.Message(entry) for entry in data]

            with self.backpack_rlock:
                self.backpack.extend(messages)
        else:
            messages = []

        if len(self.backpack) >= Config.delivery_man_capacity:
            self._incr_overloaded_cnt()

        if len(self.backpack) >= Config.delivery_man_capacity or (
            (time.time() - self.last_flush) >= Config.delivery_man_flush_interval.total_seconds()
        ):
            delivered = self._deliver_to_target_tcp()
            if not delivered:
                return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if redis_exception is not None:
            return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if len(messages) == xread:
            return Periodic.PeriodicResult.REPEAT_IMMEDIATELY

        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE

    def _deliver_to_target_tcp(self) -> bool:
        """Handle [Delivery man] -> [Destination location: target TCP].

        :return: true if all data was processed (delivered or dropped), false otherwise
        """
        if len(self.backpack) == 0:
            return True

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # Synchronize access to the backpack:
            #   collect bytes, connect to the target TCP, empty the backpack.
            # If these operations are successful, the data will be processed.
            # If any of these operations fail, end the method by returning false.
            with self.backpack_rlock:
                if len(self.backpack) == 0:
                    return True

                data = b"\n".join(message.data for message in self.backpack) + b"\n"
                message_ids = [message.message_id for message in self.backpack]

                try:
                    sock.settimeout(Config.socket_connect_timeout.total_seconds())
                    sock.connect(Config.target_tcp_address)
                except OSError as e:
                    # Connect error is not a 'final problem', it will be retried later.
                    # Return false as the data was not processed.
                    print_exception(e)
                    return False

                self.last_flush = time.time()
                self.backpack = []

            # Socket is connected, deliver the data or drop it.
            try:
                sock.settimeout(Config.socket_timeout.total_seconds())
                sock.sendall(data)

                # Send one more empty line after a while.
                # Make sure that the server did not close the connection
                # (questdb will do that asynchronously if the data was incorrect).
                # https://github.com/questdb/questdb/blob/7.2.1/core/src/main/java/io/questdb/network/AbstractIODispatcher.java#L149
                time.sleep(0.050)
                sock.sendall(b"\n")

                sock.shutdown(socket.SHUT_RDWR)
                sock.close()

                delivered = True
            except OSError as e:
                # Error while sending data is a 'final problem', it will not be retried.
                # Data may be corrupted, so it is better to drop it.
                # Print exception, mark data as not delivered and continue.
                print_exception(e)
                delivered = False

        # At this pont data was processed - delivered or dropped.
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
            print_exception(e)

        # Return true as the data was processed.
        return True

    def _incr_overloaded_cnt(self) -> None:
        try:
            self.r.incr(Config.counter_delivery_man_overloaded, 1)
        except redis.exceptions.RedisError as e:
            print_exception(e)


def status(parsel_collector: TCPParcelCollector, delivery_man: DeliveryMan, r: redis.Redis) -> Periodic.PeriodicResult:
    try:
        threading_active_count = threading.active_count()

        parcel_collector_backpack = len(parsel_collector.backpack)
        delivery_man_backpack = len(delivery_man.backpack)

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

        print_(
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
        print_exception(e)
        return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

    else:
        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE


def wait_for_redis(r: redis.Redis) -> bool:
    must_end = time.time() + Config.redis_startup_timeout.total_seconds()

    while True:
        try:
            r.ping()

        except (redis.exceptions.ConnectionError, ConnectionError) as e:
            print_exception(e)

            if time.time() > must_end:
                return False

            time.sleep(Config.redis_startup_retry_interval.total_seconds())

        else:
            return True


def redis_init(r: redis.Redis) -> bool:
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
        print_exception(e)
        return False

    else:
        return True


def main() -> int:
    print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = configure_sigterm_handler()

    r = redis.Redis(
        host=Config.redis_address[0],
        port=Config.redis_address[1],
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

    # [Pickup location: my TCP] -> [Parcel collector]
    tcp_parcel_collector = TCPParcelCollector(r, sigterm_threading_event)
    tcp_server = socketserver.TCPServer(Config.my_tcp_bind_address, tcp_parcel_collector.handler_factory)
    webhook_server_thread = VerboseThread(target=tcp_server.serve_forever, daemon=True)
    webhook_server_thread.start()

    # [Parcel collector] -> [Warehouse: Redis stream]
    parcel_collector_periodic_flush = Periodic(
        tcp_parcel_collector.deliver_to_warehouse,
        Config.parcel_collector_flush_interval,
        sigterm_threading_event,
    )
    parcel_collector_periodic_flush.start()

    # [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP]
    delivery_man = DeliveryMan(r)
    delivery_man_periodic_collect = Periodic(
        delivery_man.collect_from_warehouse_and_occasionally_deliver_to_target_tcp,
        Config.delivery_man_collect_interval,
        sigterm_threading_event,
    )
    delivery_man_periodic_collect.start()

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
