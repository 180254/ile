#!venv/bin/python3
import abc
import dataclasses
import datetime
import enum
import os
import sys
import typing
from typing import TYPE_CHECKING

import requests_cache

import ile_shared_tools

if TYPE_CHECKING:
    import requests

RFC_5322_FORMAT = "%a, %d %b %Y %H:%M:%S %Z"


@dataclasses.dataclass
class ForecastDetails:
    name: str
    time_diff: datetime.timedelta
    max_skew: datetime.timedelta

    def at_what_time(self) -> datetime.datetime:
        return datetime.datetime.now(tz=datetime.UTC) + self.time_diff


class Forecast(enum.Enum):
    CURRENT = ForecastDetails("current", datetime.timedelta(), datetime.timedelta(minutes=30))
    FORECAST_6H = ForecastDetails("forecast_6h", datetime.timedelta(hours=6), datetime.timedelta(minutes=30))
    FORECAST_12H = ForecastDetails("forecast_12h", datetime.timedelta(hours=12), datetime.timedelta(hours=1))
    FORECAST_24H = ForecastDetails("forecast_24h", datetime.timedelta(hours=24), datetime.timedelta(hours=2))
    FORECAST_3D = ForecastDetails("forecast_3d", datetime.timedelta(days=3), datetime.timedelta(hours=6))
    FORECAST_7D = ForecastDetails("forecast_7d", datetime.timedelta(days=7), datetime.timedelta(hours=6))


class Env:
    ILE_IWI_LOCATIONS: str = os.environ.get("ILE_IWI_LOCATIONS", "51.7794,19.4475")
    ILE_IWI_MODES: str = os.environ.get("ILE_IWI_MODES", ",".join([forecast.name for forecast in Forecast]))
    ILE_USER_AGENT: str = os.environ.get("ILE_IWI_USER_AGENT", "ile_weather_info/0.0.1 https://github.com/180254/ile")
    ILE_IWI_HTTP_TIMEOUT: str = os.environ.get("ILE_IWI_HTTP_TIMEOUT", "10")
    ILE_SCRAPE_INTERVAL: str = os.environ.get("ILE_IWI_SCRAPE_INTERVAL", "1800")
    ILE_IWI_BACKOFF_STRATEGY: str = os.environ.get("ILE_IWI_BACKOFF_STRATEGY", "60,300")


@dataclasses.dataclass
class Location:
    lat: float
    lon: float


class Config:
    locations: typing.Sequence[Location] = [
        Location(*[float(x) for x in location.split(",")]) for location in Env.ILE_IWI_LOCATIONS.split(";") if location
    ]

    modes: typing.Sequence[Forecast] = [Forecast[mode] for mode in Env.ILE_IWI_MODES.split(",") if mode]

    user_agent: str = Env.ILE_USER_AGENT
    http_timeout: int = int(Env.ILE_IWI_HTTP_TIMEOUT)
    scrape_interval_seconds: int = int(Env.ILE_SCRAPE_INTERVAL)
    backoff_strategy_seconds: typing.Sequence[float] = [float(x) for x in Env.ILE_IWI_BACKOFF_STRATEGY.split(",") if x]


@dataclasses.dataclass
class WeatherInfo:
    lat: float
    lon: float

    timestamp: datetime.datetime
    forecast: Forecast

    temperature_celsius: float
    humidity_percent: float
    pressure_hpa: float
    wind_meters_per_second: float

    def to_influxdb_line_protocol(self) -> str:
        nano = "000000000"
        return (
            f"ile_weather_info,lat={self.lat:.4f},lon={self.lon:.4f},mode={self.forecast.value.name} "
            f"temperature={self.temperature_celsius},"
            f"humidity={self.humidity_percent},"
            f"pressure={self.pressure_hpa},"
            f"wind={self.wind_meters_per_second} "
            f"{int(self.timestamp.timestamp())}{nano}\n"
        )


@dataclasses.dataclass
class WeatherInfoResponse:
    weather_info: list[WeatherInfo]
    expires_at: datetime.datetime | None


class WeatherProvider(abc.ABC):
    @abc.abstractmethod
    def get_weather_info(self, lat: float, lon: float) -> WeatherInfoResponse:
        pass


class MetNoWeatherProvider(WeatherProvider):
    def __init__(self, user_agent: str, session: requests_cache.CachedSession) -> None:
        self._url: str = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

        # https://api.met.no/doc/TermsOfService says:
        # - "You must identify yourself"
        # Please see "Identification" section for more information.
        self._user_agent: str = user_agent

        # https://api.met.no/doc/TermsOfService says:
        # - "You must not cause unnecessary traffic"
        # Please see "Traffic" section for more information.
        self._session: requests.Session = session

    def get_weather_info(self, lat: float, lon: float) -> WeatherInfoResponse:
        """Raises :class:`HTTPError`, if one occurred."""
        # https://api.met.no/doc/TermsOfService says:
        # - "When using requests with latitude/longitude, truncate all coordinates to max 4 decimals. "
        params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
        headers = {"User-Agent": self._user_agent}

        response = self._session.get(self._url, headers=headers, params=params)
        response.raise_for_status()
        response_json = response.json()

        # https://api.met.no/doc/TermsOfService says:
        # - "Do not ask too often, and don't repeat requests until the time indicated in the Expires response header."
        expires = datetime.datetime.strptime(response.headers["Expires"], RFC_5322_FORMAT).replace(tzinfo=datetime.UTC)

        results = [self._get_weather_info(lat, lon, response_json, forecast) for forecast in Forecast]
        results_not_none = [result for result in results if result is not None]
        return WeatherInfoResponse(results_not_none, expires)

    @staticmethod
    def _get_weather_info(lat: float, lon: float, response: dict, forecast: Forecast) -> WeatherInfo | None:
        forecast_dt = forecast.value.at_what_time()
        forecast_dt_max_skew = forecast.value.max_skew

        timeseries = response["properties"]["timeseries"]
        timeseries_dt = [datetime.datetime.fromisoformat(data_i["time"]) for data_i in timeseries]

        # find most suitable timeseries
        ts_index = 0
        ts_skew = abs(timeseries_dt[ts_index] - forecast_dt)
        for i in range(1, len(timeseries)):
            skew = abs(timeseries_dt[i] - forecast_dt)
            if skew < ts_skew:
                ts_index = i
                ts_skew = skew

        if ts_skew > forecast_dt_max_skew:
            print("s")
            ile_shared_tools.print_debug(
                lambda: (
                    f"Skipped {forecast.value.name} for {lat:.4f},{lon:.4f}: "
                    f"skew={ts_skew}, forecast_dt_max_skew={forecast_dt_max_skew}"
                )
            )
            # there are no timeseries close enough to forecast_dt
            return None

        time_dt = timeseries_dt[ts_index]
        temperature_celsius = timeseries[ts_index]["data"]["instant"]["details"]["air_temperature"]
        humidity_percent = timeseries[ts_index]["data"]["instant"]["details"]["relative_humidity"]
        pressure_hpa = timeseries[ts_index]["data"]["instant"]["details"]["air_pressure_at_sea_level"]
        wind_meters_per_second = timeseries[ts_index]["data"]["instant"]["details"]["wind_speed"]

        return WeatherInfo(
            lat, lon, time_dt, forecast, temperature_celsius, humidity_percent, pressure_hpa, wind_meters_per_second
        )


def task(weather_provider: WeatherProvider) -> datetime.datetime:
    expires_at = datetime.datetime.now(datetime.UTC)

    ilp_data = ""
    for location in Config.locations:
        response = weather_provider.get_weather_info(location.lat, location.lon)

        for weather_info in response.weather_info:
            ilp_data += weather_info.to_influxdb_line_protocol()

        if response.expires_at is not None:
            expires_at = max(expires_at, response.expires_at)

    ile_shared_tools.write_ilp_to_questdb(ilp_data)
    return expires_at


def main() -> int:
    ile_shared_tools.print_("Config" + str(vars(Config)), file=sys.stderr)

    sigterm_threading_event = ile_shared_tools.configure_sigterm_handler()

    with requests_cache.CachedSession(
        cache_name="data_weather/ilw_http_cache",
        backend="sqlite",
        cache_control=True,
        expire_after=datetime.timedelta(hours=1),
    ) as session:
        met_no_provider = MetNoWeatherProvider(Config.user_agent, session)

        backoff_idx = -1
        while True:
            try:
                response_expires_at = task(met_no_provider)
                backoff_idx = -1

                response_expires_seconds = (response_expires_at - datetime.datetime.now(datetime.UTC)).total_seconds()
                wait_seconds = max(response_expires_seconds, Config.scrape_interval_seconds)

                ile_shared_tools.print_debug(
                    lambda: f"Sleeping for {wait_seconds} seconds, data expires in {response_expires_seconds} seconds."
                )
                if sigterm_threading_event.wait(wait_seconds):
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
