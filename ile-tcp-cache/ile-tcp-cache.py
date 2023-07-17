#!venv/bin/python3
import collections
import datetime
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
    ITC_NY_TCP_BIND_PORT: str = os.environ.get("ITC_NY_TCP_BIND_PORT", "9999")

    ITC_TARGET_TCP_HOST: str = os.environ.get("ITC_TARGET_TCP_HOST", "127.0.0.1")
    ITC_TARGET_TCP_PORT: str = os.environ.get("ITC_TARGET_TCP_PORT", "9009")

    ITC_REDIS_HOST: str = os.environ.get("ITC_REDIS_HOST", "127.0.0.1")
    ITC_REDIS_PORT: str = os.environ.get("ITC_REDIS_PORT", "6379")
    ITC_REDIS_DB: str = os.environ.get("ITC_REDIS_DB", "0")
    ITC_REDIS_PASSWORD: Optional[str] = os.environ.get("ITC_REDIS_PASSWORD", None)

    ITC_REDIS_STREAM_NAME: str = os.environ.get("ITC_REDIS_STREAM_NAME", "itc")
    ITC_REDIS_STREAM_GROUP_NAME: str = os.environ.get("ITC_REDIS_STREAM_GROUP_NAME", "itc")
    ITC_REDIS_STREAM_CONSUMER_NAME: str = os.environ.get("ITC_REDIS_STREAM_CONSUMER_NAME", "itc")

    ITC_HITS_ON_TARGET_CNT_NAME: str = os.environ.get("ITC_HITS_ON_TARGET_CNT_NAME", "itc:hitsontarget")
    ITC_REDIS_DELIVERED_CNT_NAME: str = os.environ.get("ITC_REDIS_DELIVERED_CNT_NAME", "itc:delivered")

    ITC_PARCEL_COLLECTOR_CAPACITY: str = os.environ.get("ITC_PARCEL_COLLECTOR_CAPACITY", "100")
    ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL: str = os.environ.get("ITC_PARCEL_COLLECTOR_FLUSH_INTERVAL", "10s")

    ITC_REDIS_STREAM_READ_COUNT: str = os.environ.get("ITC_REDIS_STREAM_READ_COUNT", "100")
    ITC_DELIVERY_MAN_CAPACITY: str = os.environ.get("ITC_DELIVERY_MAN_CAPACITY", "1000")
    ITC_DELIVERY_MAN_COLLECT_INTERVAL: str = os.environ.get("ITC_DELIVERY_MAN_COLLECT_INTERVAL", "10s")
    ITC_DELIVERY_MAN_FLUSH_INTERVAL: str = os.environ.get("ITC_DELIVERY_MAN_FLUSH_INTERVAL", "60s")

    ITC_STATUS_INTERVAL = os.environ.get("ITC_STATUS_INTERVAL", "5s")

    ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS = os.environ.get("ITC_REDIS_EXCEPTIONS_BACKOFF", "1.1,1.5,2.0,5.0")


class Config:
    socket_connect_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_CONNECT_TIMEOUT)
    socket_timeout: datetime.timedelta = duration(Env.ITC_SOCKET_TIMEOUT)

    my_tcp_bind_address: Tuple[str, int] = (Env.ITC_MY_TCP_BIND_HOST, int(Env.ITC_NY_TCP_BIND_PORT))
    target_tcp_address: Tuple[str, int] = (Env.ITC_TARGET_TCP_HOST, int(Env.ITC_TARGET_TCP_PORT))

    redis_host: str = Env.ITC_REDIS_HOST
    redis_port: int = int(Env.ITC_REDIS_PORT)
    redis_db: int = int(Env.ITC_REDIS_DB)
    redis_password: Optional[str] = Env.ITC_REDIS_PASSWORD

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

    status_interval: datetime.timedelta = duration(Env.ITC_STATUS_INTERVAL)

    periodic_failure_backoff_multipliers: List[float] = list(
            map(float, filter(None, Env.ITC_PERIODIC_FAILURE_BACKOFF_MULTIPLIERS.split(","))))


def print_(*args, **kwargs) -> None:
    timestamp = datetime.datetime.now(datetime.timezone.utc).replace(
            microsecond=0).isoformat()
    new_args = (timestamp,) + args
    print(*new_args, **kwargs)


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


def configure_sigterm_handler() -> threading.Event:
    sigterm_cnt = [0]
    sigterm_threading_event = threading.Event()

    def sigterm_handler(signal_number, _):
        signal_name = signal.Signals(signal_number).name

        sigterm_cnt[0] += 1
        if sigterm_cnt[0] == 1:
            print_(f"Program interrupted by the {signal_name}, graceful shutdown in progress.", file=sys.stderr)
            sigterm_threading_event.set()
        else:
            print_(f"Program interrupted by the {signal_name} again, forced shutdown in progress.", file=sys.stderr)
            sys.exit(-1)

    for some_signal in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(some_signal, sigterm_handler)

    return sigterm_threading_event


def periodic(func: Callable[..., bool], interval_seconds: float, backoff_idx: int, *args, **kwargs) -> None:
    """
    Run func(*args, **kwargs) every interval seconds.

    The function must return a bool indicating whether it was successful.
    Interval is increased according to the backoff policy if the function fails or ends with an exception.
    """
    try:
        func_succeed = func(*args, **kwargs)
    except BaseException as e:
        print_exception(e)
        func_succeed = False

    if func_succeed:
        backoff_idx = -1
        delay = interval_seconds
    else:
        backoff_idx = min(backoff_idx + 1, len(Config.periodic_failure_backoff_multipliers) - 1)
        delay = Config.periodic_failure_backoff_multipliers[backoff_idx] * interval_seconds

    timer = threading.Timer(delay, periodic, args=(func, interval_seconds, backoff_idx, *args), kwargs=kwargs)
    timer.daemon = True
    timer.start()


class TCPParcelCollector(socketserver.StreamRequestHandler):
    backpack: Deque[str] = collections.deque(maxlen=Config.parcel_collector_capacity)

    def handle(self):
        self.request.setblocking(0)

        while True:
            data = self.rfile.readline()
            if not data:
                break

            data = data.decode("utf-8").strip()
            if not data:
                break

            self.backpack.append(data)

            if len(self.backpack) >= Config.parcel_collector_capacity:
                flush_thread = threading.Thread(target=self.deliver_to_warehouse, daemon=True)
                flush_thread.start()

    @classmethod
    def deliver_to_warehouse(cls, r: redis.Redis) -> bool:
        data = None
        try:
            flushed = 0
            while cls.backpack and flushed < Config.parcel_collector_capacity:
                try:
                    data = cls.backpack.popleft()
                except IndexError:
                    break

                r.xadd(name=Config.redis_stream_name, id="*", fields={"data": data})
                flushed += 1
            return True
        except redis.exceptions.RedisError as e:
            print_exception(e)
            if data:
                cls.backpack.appendleft(data)
            return False


class DeliveryMan:

    def __init__(self, r: redis.Redis) -> None:
        self.r = r
        # backpack = [(message_id, {field: value}), ...]
        self.backpack: List[Tuple[str, Dict]] = []
        self.backpack_rlock = threading.RLock()
        self.first_day_at_work = True
        self.last_delivery = time.time()
        super().__init__()

    def init(self) -> bool:
        try:
            init_id = self.r.xadd(name=Config.redis_stream_name, id="*", fields={"init": "1"})
            self.r.xdel(Config.redis_stream_name, init_id)

            xinfo_groups = self.r.xinfo_groups(name=Config.redis_stream_name)
            if not any(xgroup["name"] == Config.redis_stream_group_name for xgroup in xinfo_groups):
                self.r.xgroup_create(name=Config.redis_stream_name,
                                     groupname=Config.redis_stream_group_name,
                                     id="0",
                                     mkstream=True)

            self.r.incr(Config.redis_hits_on_target_cnt_name, 0)
            self.r.incr(Config.redis_delivered_cnt_name, 0)
            return True
        except redis.exceptions.RedisError as e:
            print_exception(e)
            return False

    def collect_from_warehouse_and_occasionally_deliver_to_target_tcp(self) -> bool:
        if len(self.backpack) >= Config.delivery_man_capacity:
            if not self._deliver_to_target_tcp():
                return False

        try:
            # xreadgroup = [(stream_name, [(message_id, {field: value}), ...]), ...]
            xreadgroup = self.r.xreadgroup(
                    groupname=Config.redis_stream_group_name,
                    consumername=Config.redis_stream_consumer_name,
                    streams={Config.redis_stream_name: "0" if self.first_day_at_work else ">"},
                    count=max(Config.redis_steam_read_count - len(self.backpack), 1),
                    noack=False)
            self.first_day_at_work = False
        except redis.exceptions.RedisError as e:
            print_exception(e)
            xreadgroup = []

        if len(xreadgroup) > 0:
            messages = xreadgroup[0][1]
            messages = filter(lambda message: bool(message[1]), messages)  # skip marked as deleted (message[1] == {})
            messages = filter(lambda message: "data" in message[1], messages)  # skip invalid messages

            with self.backpack_rlock:
                self.backpack.extend(messages)

        if len(self.backpack) >= Config.delivery_man_capacity or \
                ((time.time() - self.last_delivery) > Config.delivery_man_flush_interval.total_seconds()):
            return self._deliver_to_target_tcp()

        return True

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


def status(collector: TCPParcelCollector, delivery_man: DeliveryMan, r: redis.Redis) -> bool:
    try:
        collector_backpack = len(collector.backpack)
        delivery_man_backpack = len(delivery_man.backpack)

        warehouse_xlen = r.xlen(name=Config.redis_stream_name)
        warehouse_xpending = r.xpending(name=Config.redis_stream_name,
                                        groupname=Config.redis_stream_group_name)["pending"]

        target_tcp_hits = r.get(Config.redis_hits_on_target_cnt_name)
        target_tcp_delivered = r.get(Config.redis_delivered_cnt_name)

        print_(f"collector.backpack: {collector_backpack} | "
               f"warehouse.xlen: {warehouse_xlen} | "
               f"warehouse.xpending: {warehouse_xpending} | "
               f"delivery_man.backpack: {delivery_man_backpack} | "
               f"target_tcp.hists: {target_tcp_hits} | "
               f"target_tcp.delivered: {target_tcp_delivered}")

        return True

    except redis.exceptions.RedisError as e:
        print_exception(e)
        return False


def main() -> int:
    print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = configure_sigterm_handler()

    r = redis.Redis(host=Config.redis_host,
                    port=Config.redis_port,
                    db=Config.redis_db,
                    password=Config.redis_password,
                    socket_timeout=Config.socket_timeout.total_seconds(),
                    socket_connect_timeout=Config.socket_connect_timeout.total_seconds(),
                    decode_responses=True)

    # [Pickup location: my TCP] -> [Parcel collector]
    tcp_server = socketserver.TCPServer(Config.my_tcp_bind_address, TCPParcelCollector)
    webhook_server_thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    webhook_server_thread.start()

    # [Parcel collector] -> [Warehouse: Redis stream]
    periodic(TCPParcelCollector.deliver_to_warehouse,
             Config.parcel_collector_flush_interval.total_seconds(),
             -1,
             r)

    # [Warehouse: Redis stream] -> [Delivery man] -> [Destination location: target TCP]
    delivery_man = DeliveryMan(r)
    if not delivery_man.init():
        return -1
    periodic(delivery_man.collect_from_warehouse_and_occasionally_deliver_to_target_tcp,
             Config.delivery_man_collect_interval.total_seconds(),
             -1)

    periodic(status,
             Config.status_interval.total_seconds(),
             -1,
             TCPParcelCollector, delivery_man, r)

    sigterm_threading_event.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
