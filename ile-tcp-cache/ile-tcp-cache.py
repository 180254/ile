#!venv/bin/python3
import collections
import datetime
import enum
import os
import re
import signal
import socket
import socketserver
import sys
import threading
import time
import traceback
import types
from typing import Tuple, List, Callable, Optional, Deque, Dict

import redis


# ile-tcp-cache is useful when the target TCP server is not always available, and you don't want to lose data.
# Use case example: telegraf on a laptop that has accesses the questdb once a day.

#      [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Redis stream]
#              -> [Delivery man] -> [Destination location: target TCP]

def duration(value: str) -> datetime.timedelta:
    """Convert values like 1m30s100ms or 3s to datetime.timedelta."""
    match = re.match(r'((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?((?P<milliseconds>\d+)ms)?', value)
    if not match:
        raise ValueError(f'Invalid duration: {value}')
    matchdict = match.groupdict()
    return datetime.timedelta(minutes=int(matchdict['minutes'] or 0),
                              seconds=int(matchdict['seconds'] or 0),
                              milliseconds=int(matchdict['milliseconds'] or 0))


class Env:
    ITC_SOCKET_CONNECT_TIMEOUT: str = os.environ.get("ITC_SOCKET_CONNECT_TIMEOUT", "5s")
    ITC_SOCKET_TIMEOUT: str = os.environ.get("ITC_SOCKET_TIMEOUT", "30s")

    ITC_MY_TCP_BIND_HOST: str = os.environ.get("ITC_MY_TCP_BIND_HOST", "127.0.0.1")
    ITC_MY_TCP_BIND_PORT: str = os.environ.get("ITC_MY_TCP_BIND_PORT", "9009")

    ITC_TARGET_TCP_HOST: str = os.environ.get("ITC_TARGET_TCP_HOST", "127.0.0.1")
    ITC_TARGET_TCP_PORT: str = os.environ.get("ITC_TARGET_TCP_PORT", "9009")

    ITC_REDIS_HOST: str = os.environ.get("ITC_REDIS_HOST", "127.0.0.1")
    ITC_REDIS_PORT: str = os.environ.get("ITC_REDIS_PORT", "6379")
    ITC_REDIS_DB: str = os.environ.get("ITC_REDIS_DB", "0")
    ITC_REDIS_PASSWORD: Optional[str] = os.environ.get("ITC_REDIS_PASSWORD", None)

    ITC_REDIS_STARTUP_TIMEOUT: str = os.environ.get("ITC_REDIS_STARTUP_TIMEOUT", "30s")
    ITC_REDIS_STARTUP_RETRY_INTERVAL: str = os.environ.get("ITC_REDIS_STARTUP_RETRY_INTERVAL", "1s")

    ITC_REDIS_STREAM_NAME: str = os.environ.get("ITC_REDIS_STREAM_NAME", "itc")
    ITC_REDIS_STREAM_GROUP_NAME: str = os.environ.get("ITC_REDIS_STREAM_GROUP_NAME", "itc")
    ITC_REDIS_STREAM_CONSUMER_NAME: str = os.environ.get("ITC_REDIS_STREAM_CONSUMER_NAME", "itc")

    ITC_HITS_ON_TARGET_CNT_NAME: str = os.environ.get("ITC_HITS_ON_TARGET_CNT_NAME", "itc:hitsontarget")
    ITC_REDIS_DELIVERED_CNT_NAME: str = os.environ.get("ITC_REDIS_DELIVERED_CNT_NAME", "itc:delivered")

    ITC_PARCEL_COLLECTOR_CAPACITY: str = os.environ.get("ITC_PARCEL_COLLECTOR_CAPACITY", "2048")
    ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL: str = os.environ.get("ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL", "60s")

    ITC_REDIS_STREAM_READ_COUNT: str = os.environ.get("ITC_REDIS_STREAM_READ_COUNT", "256")
    ITC_DELIVERY_MAN_CAPACITY: str = os.environ.get("ITC_DELIVERY_MAN_CAPACITY", "256")

    ITC_DELIVERY_MAN_COLLECT_INTERVAL: str = os.environ.get("ITC_DELIVERY_MAN_COLLECT_INTERVAL", "60s")
    ITC_DELIVERY_MAN_FLUSH_INTERVAL: str = os.environ.get("ITC_DELIVERY_MAN_FLUSH_INTERVAL", "60s")

    ITC_PARCEL_COLLECTOR_OVERLOADED_CNT_NAME: str = os.environ.get("ITC_PARCEL_COLLECTOR_OVERLOADED_CNT_NAME",
                                                                   "itc:pcoverloaded")
    ITC_DELIVERY_MAN_OVERLOADED_CNT_NAME: str = os.environ.get("ITC_DELIVERY_MAN_OVERLOADED_CNT_NAME",
                                                               "itc:dmcapacityreached")

    ITC_STATUS_INTERVAL = os.environ.get("ITC_STATUS_INTERVAL", "60s")

    ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS = os.environ.get("ITC_REDIS_EXCEPTIONS_BACKOFF", "1.1,1.5,2.0,5.0")


class Config:
    socket_connect_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_CONNECT_TIMEOUT)
    socket_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_TIMEOUT)

    my_tcp_bind_address: Tuple[str, int] = (Env.ITC_MY_TCP_BIND_HOST, int(Env.ITC_MY_TCP_BIND_PORT))
    target_tcp_address: Tuple[str, int] = (Env.ITC_TARGET_TCP_HOST, int(Env.ITC_TARGET_TCP_PORT))

    redis_address: Tuple[str, int] = (Env.ITC_REDIS_HOST, int(Env.ITC_REDIS_PORT))
    redis_db: int = int(Env.ITC_REDIS_DB)
    redis_password: Optional[str] = Env.ITC_REDIS_PASSWORD

    redis_startup_timeout: datetime.timedelta = duration(Env.ITC_REDIS_STARTUP_TIMEOUT)
    redis_startup_retry_interval: datetime.timedelta = duration(Env.ITC_REDIS_STARTUP_RETRY_INTERVAL)

    redis_stream_name: str = Env.ITC_REDIS_STREAM_NAME
    redis_stream_group_name: str = Env.ITC_REDIS_STREAM_GROUP_NAME
    redis_stream_consumer_name: str = Env.ITC_REDIS_STREAM_CONSUMER_NAME

    redis_delivered_cnt_name: str = Env.ITC_REDIS_DELIVERED_CNT_NAME
    redis_hits_on_target_cnt_name: str = Env.ITC_HITS_ON_TARGET_CNT_NAME

    parcel_collector_capacity: int = int(Env.ITC_PARCEL_COLLECTOR_CAPACITY)
    parcel_collector_flush_interval: datetime.timedelta = duration(Env.ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL)

    redis_steam_read_count: int = int(Env.ITC_REDIS_STREAM_READ_COUNT)
    delivery_man_capacity: int = int(Env.ITC_DELIVERY_MAN_CAPACITY)
    delivery_man_collect_interval: datetime.timedelta = duration(Env.ITC_DELIVERY_MAN_COLLECT_INTERVAL)
    delivery_man_flush_interval: datetime.timedelta = duration(Env.ITC_DELIVERY_MAN_FLUSH_INTERVAL)

    parcel_collector_overloaded_cnt_name: str = Env.ITC_PARCEL_COLLECTOR_OVERLOADED_CNT_NAME
    delivery_man_overloaded_cnt_name: str = Env.ITC_DELIVERY_MAN_OVERLOADED_CNT_NAME

    status_interval: datetime.timedelta = duration(Env.ITC_STATUS_INTERVAL)

    periodic_failure_backoff_multipliers: List[float] = \
        list(map(float, filter(None, Env.ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS.split(","))))


# noinspection DuplicatedCode
def print_(*args, **kwargs) -> None:
    timestamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    new_args = (timestamp,) + args
    print(*new_args, **kwargs)


# noinspection DuplicatedCode
def print_exception(exception: BaseException) -> None:
    exc_traceback: Optional[types.TracebackType] = exception.__traceback__

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
    sigterm_cnt = [0]
    sigterm_threading_event = threading.Event()

    def sigterm_handler(signal_number, _):
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


class Periodic:
    class PeriodicResult(enum.Enum):
        REPEAT_ON_SCHEDULE = enum.auto()
        REPEAT_WITH_BACKOFF = enum.auto()
        REPEAT_IMMEDIATELY = enum.auto()

    def __init__(self, func: Callable[[], PeriodicResult], interval: datetime.timedelta,
            sigterm_threading_event: threading.Event) -> None:
        super().__init__()
        self.func = func
        self.interval = interval
        self.sigterm_threading_event = sigterm_threading_event
        self.backoff_idx = -1

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=False).start()

    def _run(self) -> None:
        try:
            result = self.func()
        except BaseException as e:
            print_exception(e)
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
            timer = threading.Timer(delay, self._run)
            timer.daemon = False
            timer.start()


class TCPParcelCollector:

    def __init__(self, r: redis.Redis, sigterm_threading_event: threading.Event) -> None:
        super().__init__()
        self.r = r
        self.sigterm_threading_event = sigterm_threading_event
        self.backpack: Deque[str] = collections.deque(maxlen=Config.parcel_collector_capacity)
        self.handler_delivery_thread: Optional[threading.Thread] = None

    class Handler(socketserver.StreamRequestHandler):
        def __init__(self, request, client_address, server, outer: 'TCPParcelCollector') -> None:
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

                if len(self.outer.backpack) >= Config.parcel_collector_capacity * 0.75 \
                        and not self.outer.sigterm_threading_event.is_set():
                    threading.Thread(target=self.outer._incr_overloaded_cnt, daemon=False).start()

                    # simple check to limit the number of threads
                    # synchronization not needed, more than one thread is allowed
                    if not self.outer.handler_delivery_thread or not self.outer.handler_delivery_thread.is_alive():
                        flush_thread = threading.Thread(target=self.outer.deliver_to_warehouse, daemon=False)
                        flush_thread.start()
                        self.outer.handler_delivery_thread = flush_thread

            except BaseException as e:
                print_exception(e)

    def handler_factory(self, request, client_address, server) -> 'TCPParcelCollector.Handler':
        return TCPParcelCollector.Handler(request, client_address, server, self)

    def deliver_to_warehouse(self) -> Periodic.PeriodicResult:
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

            return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE

        except redis.exceptions.RedisError as e:
            print_exception(e)
            if data:
                self.backpack.appendleft(data)
            return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

    def _incr_overloaded_cnt(self) -> None:
        try:
            self.r.incr(Config.parcel_collector_overloaded_cnt_name, 1)
        except redis.exceptions.RedisError as e:
            print_exception(e)


class DeliveryMan:

    def __init__(self, r: redis.Redis) -> None:
        super().__init__()
        self.r = r
        # backpack = [(message_id, {field: value}), ...]
        self.backpack: List[Tuple[str, Dict]] = []
        self.backpack_rlock = threading.RLock()
        self.last_delivery = time.time()

    def collect_from_warehouse_and_occasionally_deliver_to_target_tcp(self) -> Periodic.PeriodicResult:
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
                    noack=False)

        except redis.exceptions.RedisError as e:
            print_exception(e)
            redis_exception = e
            xreadgroup = []

        if len(xreadgroup) > 0:
            messages = xreadgroup[0][1]
            messages = filter(lambda message: bool(message[1]), messages)  # skip marked as deleted (message[1] == {})
            messages = filter(lambda message: "data" in message[1], messages)  # skip invalid messages
            messages = list(messages)

            with self.backpack_rlock:
                self.backpack.extend(messages)
        else:
            messages = []

        if len(self.backpack) >= Config.delivery_man_capacity:
            self._incr_overloaded_cnt()

        if len(self.backpack) >= Config.delivery_man_capacity or \
                ((time.time() - self.last_delivery) > Config.delivery_man_flush_interval.total_seconds()):
            if not self._deliver_to_target_tcp():
                return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if redis_exception is not None:
            return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF

        if len(messages) == xread:
            return Periodic.PeriodicResult.REPEAT_IMMEDIATELY

        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE

    def _deliver_to_target_tcp(self) -> bool:
        if len(self.backpack) == 0:
            return True

        with self.backpack_rlock:
            data = "\n".join(message[1]["data"] for message in self.backpack)
            data += "\n"

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(Config.socket_connect_timeout.total_seconds())
                    sock.connect(Config.target_tcp_address)
                    sock.settimeout(Config.socket_timeout.total_seconds())
                    sock.sendall(data.encode())
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()

            except socket.error as e:
                print_exception(e)
                return False

            self.last_delivery = time.time()
            message_ids = [item[0] for item in self.backpack]
            self.backpack = []

        try:
            self.r.xack(Config.redis_stream_name, Config.redis_stream_group_name, *message_ids)
            self.r.xdel(Config.redis_stream_name, *message_ids)
            self.r.incr(Config.redis_hits_on_target_cnt_name, 1)
            self.r.incr(Config.redis_delivered_cnt_name, len(message_ids))

        except redis.exceptions.RedisError as e:
            print_exception(e)

        return True

    def _incr_overloaded_cnt(self) -> None:
        try:
            self.r.incr(Config.delivery_man_overloaded_cnt_name, 1)
        except redis.exceptions.RedisError as e:
            print_exception(e)


def status(parsel_collector: TCPParcelCollector, delivery_man: DeliveryMan, r: redis.Redis) -> Periodic.PeriodicResult:
    try:
        threading_active_count = threading.active_count()

        parcel_collector_backpack = len(parsel_collector.backpack)
        delivery_man_backpack = len(delivery_man.backpack)

        warehouse_xlen = r.xlen(name=Config.redis_stream_name)
        warehouse_xpending = r.xpending(name=Config.redis_stream_name,
                                        groupname=Config.redis_stream_group_name)["pending"]

        target_tcp_hits = r.get(Config.redis_hits_on_target_cnt_name)
        target_tcp_delivered = r.get(Config.redis_delivered_cnt_name)

        parcel_collector_overloaded = r.get(Config.parcel_collector_overloaded_cnt_name)
        delivery_man_overloaded = r.get(Config.delivery_man_overloaded_cnt_name)

        print_(f"threading.active_count: {threading_active_count} | "
               f"parcel_collector.backpack: {parcel_collector_backpack} | "
               f"warehouse.xlen: {warehouse_xlen} | "
               f"warehouse.xpending: {warehouse_xpending} | "
               f"delivery_man.backpack: {delivery_man_backpack} | "
               f"target_tcp.hists: {target_tcp_hits} | "
               f"target_tcp.delivered: {target_tcp_delivered} | "
               f"parcel_collector.overloaded: {parcel_collector_overloaded} | "
               f"delivery_man.overloaded: {delivery_man_overloaded}")

        return Periodic.PeriodicResult.REPEAT_ON_SCHEDULE

    except redis.exceptions.RedisError as e:
        print_exception(e)
        return Periodic.PeriodicResult.REPEAT_WITH_BACKOFF


def wait_for_redis(r: redis.Redis) -> bool:
    must_end = time.time() + Config.redis_startup_timeout.total_seconds()

    while True:
        try:
            r.ping()
            return True
        except (redis.exceptions.ConnectionError, ConnectionError) as e:
            print_exception(e)

            if time.time() > must_end:
                return False

            time.sleep(Config.redis_startup_retry_interval.total_seconds())


def redis_init(r: redis.Redis) -> bool:
    try:
        init_id = r.xadd(name=Config.redis_stream_name, id="*", fields={"init": "1"})
        r.xdel(Config.redis_stream_name, init_id)

        xinfo_groups = r.xinfo_groups(name=Config.redis_stream_name)
        for xgroup in xinfo_groups:
            r.xgroup_destroy(name=Config.redis_stream_name, groupname=xgroup["name"])

        r.xgroup_create(name=Config.redis_stream_name, groupname=Config.redis_stream_group_name, id="0")

        r.incr(Config.redis_hits_on_target_cnt_name, 0)
        r.incr(Config.redis_delivered_cnt_name, 0)

        r.incr(Config.delivery_man_overloaded_cnt_name, 0)
        r.incr(Config.parcel_collector_overloaded_cnt_name, 0)

        return True

    except redis.exceptions.RedisError as e:
        print_exception(e)
        return False


def main() -> int:
    print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = configure_sigterm_handler()

    r = redis.Redis(host=Config.redis_address[0],
                    port=Config.redis_address[1],
                    db=Config.redis_db,
                    password=Config.redis_password,
                    socket_timeout=Config.socket_timeout.total_seconds(),
                    socket_connect_timeout=Config.socket_connect_timeout.total_seconds(),
                    decode_responses=True)

    if not wait_for_redis(r):
        return -1

    if not redis_init(r):
        return -1

    # [Pickup location: my TCP] -> [Parcel collector]
    tcp_parcel_collector = TCPParcelCollector(r, sigterm_threading_event)
    tcp_server = socketserver.TCPServer(Config.my_tcp_bind_address, tcp_parcel_collector.handler_factory)
    webhook_server_thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    webhook_server_thread.start()

    # [Parcel collector] -> [Warehouse: Redis stream]
    parcel_collector_periodic_flush = Periodic(tcp_parcel_collector.deliver_to_warehouse,
                                               Config.parcel_collector_flush_interval,
                                               sigterm_threading_event)
    parcel_collector_periodic_flush.start()

    # [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP]
    delivery_man = DeliveryMan(r)
    delivery_man_periodic_collect = Periodic(delivery_man.collect_from_warehouse_and_occasionally_deliver_to_target_tcp,
                                             Config.delivery_man_collect_interval, sigterm_threading_event)
    delivery_man_periodic_collect.start()

    periodic_status = Periodic(lambda: status(tcp_parcel_collector, delivery_man, r), Config.status_interval,
                               sigterm_threading_event)
    periodic_status.start()

    sigterm_threading_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
