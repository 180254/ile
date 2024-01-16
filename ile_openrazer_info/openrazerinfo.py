import json
import os
import sys
import time
import typing

import openrazer.client

import ile_shared_tools

# ile_openrazer is a program that collects data about razer-manufactured devices and stores it to the db.
# Useful for monitoring the battery state of the devices.


class Env:
    ILE_IOR_SCRAPE_INTERVAL: str = os.environ.get("ILE_IOR_SCRAPE_INTERVAL", "300")
    ILE_IOR_BACKOFF_STRATEGY: str = os.environ.get("ILE_IOR_BACKOFF_STRATEGY", "60,300")


class Config:
    scrape_interval_seconds: int = int(Env.ILE_IOR_SCRAPE_INTERVAL)
    backoff_strategy_seconds: typing.Sequence[float] = [float(x) for x in Env.ILE_IOR_BACKOFF_STRATEGY.split(",") if x]


def task() -> None:
    timestamp = int(time.time())
    nano = "000000000"

    data = ""
    device_manager = openrazer.client.DeviceManager()
    for device in device_manager.devices:
        device_name = device.name
        device_type = device.type
        device_serial = device.serial
        device_firmware_version = device.firmware_version
        device_driver_version = device.driver_version
        device_battery_level = device.battery_level
        device_is_charging = device.is_charging

        # symbolset
        device_name2 = device_name.replace(" ", "\\ ") if device_name else None
        device_type2 = device_type.replace(" ", "\\ ") if device_type else None
        device_serial2 = device_serial.replace(" ", "\\ ") if device_serial else None

        # columnset
        device_firmware_version2 = json.dumps(device_firmware_version) if device_firmware_version else None
        device_driver_version2 = json.dumps(device_driver_version) if device_driver_version else None
        device_battery_level2 = device_battery_level
        device_is_charging2 = device_is_charging

        data += (
            "openrazer_info1,"
            f"device_name={device_name2},"
            f"device_type={device_type2},"
            f"device_id={device_serial2} "
            f"firmware_version={device_firmware_version2},"
            f"driver_version={device_driver_version2},"
            f"battery_level={device_battery_level2},"
            f"is_charging={device_is_charging2} "
            f"{timestamp}{nano}\n"
        )

    ile_shared_tools.write_ilp_to_questdb(data)


def main() -> int:
    ile_shared_tools.print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = ile_shared_tools.configure_sigterm_handler()

    backoff_idx = -1
    while True:
        try:
            task()
            backoff_idx = -1

            if sigterm_threading_event.wait(Config.scrape_interval_seconds):
                break

        except Exception as exception:
            ile_shared_tools.print_exception(exception)
            backoff_idx = max(0, min(backoff_idx + 1, len(Config.backoff_strategy_seconds) - 1))
            backoff = Config.backoff_strategy_seconds[backoff_idx]
            if sigterm_threading_event.wait(backoff):
                break

    return 0


if __name__ == "__main__":
    sys.exit(main())
