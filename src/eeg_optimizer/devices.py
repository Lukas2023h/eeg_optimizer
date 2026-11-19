"""Flexible loads (devices) of a household: typed model and validation.

``config.py`` keeps ``households[].devices`` as raw mappings; this module turns them
into :class:`ModulatingDevice` / :class:`ProgramDevice` (docs/architecture.md §3.3)::

    devices:
      - {id: ev, kind: modulating, power_kw: 11.0, energy_kwh_per_day: 8.0,
         window: {start: "18:00", end: "07:00"}, baseline_start: "18:00"}
      - {id: wm, kind: program, power_kw: 2.0, duration_min: 90,
         window: {start: "08:00", end: "20:00"}, baseline_start: "19:00"}

* ``modulating`` (EV, boiler, heat pump): power 0…``power_kw``, fixed energy per day.
  Optional ``min_run_min`` (contiguous run time) and ``min_power_kw`` (minimum power
  while running); both are off by default.
* ``program`` (washing machine, dishwasher): ``power_kw`` for ``duration_min`` in one
  block; only the start is free. Energy per day = ``power_kw`` * duration.

Times are ``"HH:MM"`` strings in local time on the 15-min grid; durations are whole
multiples of 15 min, so every device maps exactly onto the internal grid. A window
whose end is not after its start (e.g. 18:00 to 07:00) crosses midnight; the task belongs
to the day the window starts. ``start == end`` is rejected (empty window is ambiguous).

Validation (all :class:`~eeg_optimizer.config.ConfigError` with key path, German):
the daily energy must be reachable at ``power_kw`` both within the whole window and
from ``baseline_start`` to the window end (the baseline runs at full power from its
start, F4-02); ``baseline_start`` must lie in the window; ``min_power_kw`` ≤ ``power_kw``;
``min_run_min`` ≤ window length; device ids are unique within a household.

Baseline schedule (F4-02, :func:`baseline_schedule`): today's unoptimized operation. Each
task (one per day, see :func:`tasks`) starts at ``baseline_start`` and runs at ``power_kw``
in consecutive intervals until its daily energy is met; a modulating device puts the rest
into one partial interval, a program runs exactly ``duration_min``. Wall-clock times are
turned into instants per day in Europe/Vienna: a time that does not exist (spring DST,
02:00 to 02:59) moves to 03:00, an ambiguous time (autumn DST) takes the first occurrence
(summer time). The run itself counts real intervals, so on 2023-03-26 a window 00:00 to
04:00 is only 3 h long and on 2023-10-29 a window 00:00 to 04:00 is 5 h long. If the
energy no longer fits into the shortened window, :class:`BaselineScheduleError` is raised;
nothing is cut silently. Only tasks whose whole window lies in the given index are
scheduled (tasks at the edges, e.g. 31.12. 18:00 to 07:00 of a year, are left out), so baseline and
optimization see the same set of tasks. The baseline may violate ``min_power_kw`` in its
last partial interval and ``min_run_min`` if the energy is met earlier; it describes the
current behaviour and is not an optimization result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from eeg_optimizer.config import CommunityConfig, ConfigError, ConfigSection
from eeg_optimizer.units import INTERVAL, KW, TIMEZONE, KWh, kw_to_kwh, validate_frame

DEVICE_COLUMN: Final = "device_kwh"
LOAD_COLUMN: Final = "load_kwh"

DeviceKind = Literal["modulating", "program"]
DEVICE_KINDS: Final[tuple[DeviceKind, ...]] = ("modulating", "program")

_MINUTES_PER_DAY: Final = 24 * 60
_GRID_MIN: Final = int(INTERVAL.total_seconds() // 60)
_TIME_PATTERN: Final = re.compile(r"^(\d{2}):(\d{2})$")
_ENERGY_TOLERANCE_KWH: Final = 1e-9


def _minute_of_day(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass(frozen=True)
class TimeWindow:
    """Daily time window ``[start, end)`` in local time; may cross midnight."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("TimeWindow: start und end dürfen nicht gleich sein")

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.minutes_after_start(self.end))

    def minutes_after_start(self, t: time) -> int:
        """Minutes from the window start to ``t`` (going forward, modulo one day)."""
        return (_minute_of_day(t) - _minute_of_day(self.start)) % _MINUTES_PER_DAY

    def contains(self, t: time) -> bool:
        """``True`` if ``t`` lies in ``[start, end)``."""
        return timedelta(minutes=self.minutes_after_start(t)) < self.duration

    def remaining_after(self, t: time) -> timedelta:
        """Time from ``t`` (inside the window) to the window end."""
        if not self.contains(t):
            raise ValueError(f"{t:%H:%M} liegt nicht im Fenster")
        return self.duration - timedelta(minutes=self.minutes_after_start(t))


@dataclass(frozen=True)
class ModulatingDevice:
    """Power freely between 0 and ``power_kw``; fixed energy per day within the window."""

    id: str
    power_kw: KW
    energy_kwh_per_day: KWh
    window: TimeWindow
    baseline_start: time
    min_run_min: int | None = None
    """Minimum contiguous run time in minutes (``None`` = off)."""
    min_power_kw: KW | None = None
    """Minimum power while running (``None`` = off)."""
    kind: Literal["modulating"] = "modulating"

    @property
    def needs_binaries(self) -> bool:
        """``True`` if the device needs integer variables (MILP instead of LP)."""
        return self.min_run_min is not None or self.min_power_kw is not None


@dataclass(frozen=True)
class ProgramDevice:
    """Fixed power for a fixed duration in one block; only the start is free."""

    id: str
    power_kw: KW
    duration_min: int
    window: TimeWindow
    baseline_start: time
    kind: Literal["program"] = "program"

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_min)

    @property
    def energy_kwh_per_day(self) -> KWh:
        return kw_to_kwh(self.power_kw, self.duration)

    @property
    def needs_binaries(self) -> bool:
        return True


Device = ModulatingDevice | ProgramDevice


# --------------------------------------------------------------------- baseline (F4-02)


class BaselineScheduleError(ValueError):
    """The baseline of a device cannot be built (e.g. energy does not fit on a DST day)."""


@dataclass(frozen=True)
class Task:
    """One daily task of a device: the window that starts on ``day`` (local date).

    ``window_start``/``window_end``/``baseline_start`` are tz-aware instants in
    Europe/Vienna on the 15-min grid; the window is ``[window_start, window_end)``.
    """

    device_id: str
    day: date
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    baseline_start: pd.Timestamp
    energy_kwh: KWh


def _local_instant(day: date, t: time) -> pd.Timestamp:
    # Nonexistent (spring) -> shifted to 03:00; ambiguous (autumn) -> first occurrence.
    naive = pd.Timestamp(datetime.combine(day, t))
    return naive.tz_localize(TIMEZONE, ambiguous=True, nonexistent="shift_forward")


def _task_for_day(device: Device, day: date) -> Task:
    window = device.window
    next_day = day + timedelta(days=1)
    end_day = next_day if window.crosses_midnight else day
    baseline_day = next_day if device.baseline_start < window.start else day
    return Task(
        device_id=device.id,
        day=day,
        window_start=_local_instant(day, window.start),
        window_end=_local_instant(end_day, window.end),
        baseline_start=_local_instant(baseline_day, device.baseline_start),
        energy_kwh=device.energy_kwh_per_day,
    )


def _checked_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    validate_frame(pd.DataFrame(index=index))
    return index


def tasks(device: Device, index: pd.DatetimeIndex) -> list[Task]:
    """All daily tasks of ``device`` whose whole window lies inside ``index``.

    ``index`` must be in the internal format (gapless 15-min grid, Europe/Vienna); its
    end is the start of the last interval plus 15 min. Tasks are ordered by day.

    Raises:
        units.FrameValidationError: if ``index`` is not in the internal format.
    """
    index = _checked_index(index)
    first, end = index[0], index[-1] + INTERVAL
    day = (first - timedelta(days=1)).date()  # a window from the day before may fit
    last_day = index[-1].date()
    result: list[Task] = []
    while day <= last_day:
        task = _task_for_day(device, day)
        if task.window_start >= first and task.window_end <= end:
            result.append(task)
        day += timedelta(days=1)
    return result


def _position(index: pd.DatetimeIndex, instant: pd.Timestamp) -> int:
    """Position of ``instant`` in ``index``; ``len(index)`` for the end of the index."""
    pos = int(index.searchsorted(instant))
    if pos < len(index) and index[pos] != instant:
        raise BaselineScheduleError(f"{instant.isoformat()} liegt nicht auf dem 15-min-Raster")
    return pos


def _run_energies(device: Device) -> np.ndarray:
    """Energy per interval of one baseline run (full power, rest in the last interval)."""
    per_interval = kw_to_kwh(device.power_kw, INTERVAL)
    if isinstance(device, ProgramDevice):
        n_intervals = device.duration_min // _GRID_MIN
        return np.full(n_intervals, per_interval, dtype=float)
    energy = device.energy_kwh_per_day
    n_full = int(np.floor((energy + _ENERGY_TOLERANCE_KWH) / per_interval))
    rest = max(energy - n_full * per_interval, 0.0)
    run: list[float] = [per_interval] * n_full
    if rest > _ENERGY_TOLERANCE_KWH:
        run.append(rest)
    return np.asarray(run, dtype=float)


def baseline_schedule(device: Device, index: pd.DatetimeIndex) -> pd.Series:
    """Baseline energy of ``device`` per interval of ``index`` (Series ``device_kwh``).

    Every task of :func:`tasks` runs from its ``baseline_start`` at ``power_kw`` until the
    daily energy is met (program: for ``duration_min``); all other intervals are 0.

    Raises:
        units.FrameValidationError: if ``index`` is not in the internal format.
        BaselineScheduleError: if a run does not end inside its window (only possible on
            the spring DST day, when the window loses one hour).
    """
    index = _checked_index(index)
    values = np.zeros(len(index), dtype=float)
    run = _run_energies(device)
    for task in tasks(device, index):
        start = _position(index, task.baseline_start)
        stop = start + len(run)
        if stop > _position(index, task.window_end):
            raise BaselineScheduleError(
                f"Gerät {device.id!r}, Tag {task.day:%Y-%m-%d}: der Baseline-Lauf ab "
                f"{task.baseline_start:%H:%M} braucht {len(run)} Intervalle und endet nach "
                f"dem Fensterende {task.window_end:%H:%M} (Zeitumstellung verkürzt das "
                "Fenster um 1 h); baseline_start früher legen oder Fenster verlängern"
            )
        values[start:stop] += run
    return pd.Series(values, index=index, name=DEVICE_COLUMN)


def baseline_loads(
    loads: Mapping[str, pd.DataFrame], devices: Mapping[str, Sequence[Device]]
) -> dict[str, pd.DataFrame]:
    """Household loads with the baseline of their flexible devices added (``load_kwh``).

    ``loads`` are the inflexible loads (exactly ``load_kwh``, internal format, one index);
    ``devices`` maps household ids to their devices (households missing there have none).

    Raises:
        BaselineScheduleError: for devices of an unknown household or a wrong load frame.
        units.FrameValidationError: if a load frame is not in the internal format.
    """
    unknown = sorted(set(devices) - set(loads))
    if unknown:
        raise BaselineScheduleError(
            f"Geräte für unbekannte Haushalte: {', '.join(unknown)} (bekannt: {', '.join(loads)})"
        )
    result: dict[str, pd.DataFrame] = {}
    for household_id, frame in loads.items():
        if list(frame.columns) != [LOAD_COLUMN]:
            raise BaselineScheduleError(
                f"Last von {household_id!r}: genau die Spalte {LOAD_COLUMN!r} erwartet, "
                f"gefunden {list(frame.columns)}"
            )
        validate_frame(frame)
        total = frame[LOAD_COLUMN].to_numpy(dtype=float).copy()
        index = pd.DatetimeIndex(frame.index)
        for device in devices.get(household_id, ()):
            total += baseline_schedule(device, index).to_numpy(dtype=float)
        result[household_id] = pd.DataFrame({LOAD_COLUMN: total}, index=index)
    return result


# --------------------------------------------------------------------------- parsing


def parse_devices(raw: Sequence[Mapping[str, Any]], path: str = "devices") -> tuple[Device, ...]:
    """Validate the ``devices`` list of one household; ids must be unique."""
    devices: list[Device] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        device = parse_device(item, item_path)
        if device.id in seen:
            raise ConfigError(f"{item_path}.id: Geräte-ID {device.id!r} ist doppelt vergeben")
        seen.add(device.id)
        devices.append(device)
    return tuple(devices)


def parse_device(raw: Mapping[str, Any], path: str) -> Device:
    """Validate one raw device mapping and build the typed device."""
    s = ConfigSection(raw, path)
    device_id = s.string("id")
    kind = s.choice("kind", DEVICE_KINDS)
    device: Device
    if kind == "modulating":
        device = _parse_modulating(s, device_id)
    else:
        device = _parse_program(s, device_id)
    s.finish()
    return device


def devices_for(config: CommunityConfig) -> dict[str, tuple[Device, ...]]:
    """Typed devices of every household (in config order, also for households without)."""
    return {
        hh.id: parse_devices(hh.devices, f"households[{i}].devices")
        for i, hh in enumerate(config.households)
    }


def _parse_modulating(s: ConfigSection, device_id: str) -> ModulatingDevice:
    power = KW(s.number("power_kw", minimum=0.0, minimum_exclusive=True))
    energy = KWh(s.number("energy_kwh_per_day", minimum=0.0, minimum_exclusive=True))
    window = _parse_window(s)
    baseline_start = _parse_baseline_start(s, window)
    min_run_min = _optional_grid_minutes(s, "min_run_min")
    raw_min_power = s.optional_number("min_power_kw", minimum=0.0, minimum_exclusive=True)
    min_power = None if raw_min_power is None else KW(raw_min_power)
    if min_power is not None and min_power > power:
        raise ConfigError(
            f"{_key(s, 'min_power_kw')}: Mindestleistung {min_power:g} kW ist größer "
            f"als die Leistung power_kw {power:g} kW"
        )

    reachable = kw_to_kwh(power, window.duration)
    if energy > reachable + _ENERGY_TOLERANCE_KWH:
        raise ConfigError(
            f"{_key(s, 'energy_kwh_per_day')}: {energy:g} kWh sind im Fenster "
            f"{_fmt_window(window)} mit {power:g} kW nicht erreichbar "
            f"(höchstens {reachable:g} kWh)"
        )
    from_baseline = kw_to_kwh(power, window.remaining_after(baseline_start))
    if energy > from_baseline + _ENERGY_TOLERANCE_KWH:
        raise ConfigError(
            f"{_key(s, 'baseline_start')}: ab {baseline_start:%H:%M} bis Fensterende sind "
            f"mit {power:g} kW nur {from_baseline:g} kWh erreichbar, gebraucht {energy:g} kWh"
        )
    if min_run_min is not None and timedelta(minutes=min_run_min) > window.duration:
        raise ConfigError(
            f"{_key(s, 'min_run_min')}: {min_run_min} min sind länger als das Fenster "
            f"{_fmt_window(window)}"
        )
    return ModulatingDevice(
        id=device_id,
        power_kw=power,
        energy_kwh_per_day=energy,
        window=window,
        baseline_start=baseline_start,
        min_run_min=min_run_min,
        min_power_kw=min_power,
    )


def _parse_program(s: ConfigSection, device_id: str) -> ProgramDevice:
    power = KW(s.number("power_kw", minimum=0.0, minimum_exclusive=True))
    duration_min = _grid_minutes(s, "duration_min")
    window = _parse_window(s)
    baseline_start = _parse_baseline_start(s, window)
    duration = timedelta(minutes=duration_min)
    if duration > window.duration:
        raise ConfigError(
            f"{_key(s, 'duration_min')}: {duration_min} min passen nicht in das Fenster "
            f"{_fmt_window(window)}"
        )
    if duration > window.remaining_after(baseline_start):
        raise ConfigError(
            f"{_key(s, 'baseline_start')}: ein Programm ab {baseline_start:%H:%M} mit "
            f"{duration_min} min endet nach dem Fensterende {window.end:%H:%M}"
        )
    return ProgramDevice(
        id=device_id,
        power_kw=power,
        duration_min=duration_min,
        window=window,
        baseline_start=baseline_start,
    )


def _parse_window(s: ConfigSection) -> TimeWindow:
    w = s.section("window")
    start = _parse_time(w, "start")
    end = _parse_time(w, "end")
    w.finish()
    if start == end:
        raise ConfigError(
            f"{w.path}: Beginn und Ende sind gleich ({start:%H:%M}), das Fenster wäre leer"
        )
    return TimeWindow(start=start, end=end)


def _parse_baseline_start(s: ConfigSection, window: TimeWindow) -> time:
    start = _parse_time(s, "baseline_start")
    if not window.contains(start):
        raise ConfigError(
            f"{_key(s, 'baseline_start')}: {start:%H:%M} liegt nicht im Fenster "
            f"{_fmt_window(window)}"
        )
    return start


def _parse_time(s: ConfigSection, key: str) -> time:
    # Unquoted 18:00 is read by YAML 1.1 as the integer 1080; string() rejects it.
    text = s.string(key)
    match = _TIME_PATTERN.match(text)
    if match is None:
        raise ConfigError(
            f'{_key(s, key)}: Uhrzeit als Text "HH:MM" erwartet (in YAML in '
            f"Anführungszeichen), gefunden {text!r}"
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ConfigError(f"{_key(s, key)}: ungültige Uhrzeit {text!r}")
    if minute % _GRID_MIN:
        raise ConfigError(
            f"{_key(s, key)}: {text} liegt nicht auf dem {_GRID_MIN}-min-Raster "
            f"(erlaubt :00, :15, :30, :45)"
        )
    return time(hour, minute)


def _grid_minutes(s: ConfigSection, key: str) -> int:
    return _check_grid_minutes(s, key, s.integer(key, minimum=_GRID_MIN))


def _optional_grid_minutes(s: ConfigSection, key: str) -> int | None:
    minutes = s.optional_integer(key, minimum=_GRID_MIN)
    return None if minutes is None else _check_grid_minutes(s, key, minutes)


def _check_grid_minutes(s: ConfigSection, key: str, minutes: int) -> int:
    if minutes % _GRID_MIN:
        raise ConfigError(f"{_key(s, key)}: {minutes} min ist kein Vielfaches von {_GRID_MIN} min")
    return minutes


def _key(s: ConfigSection, key: str) -> str:
    return f"{s.path}.{key}" if s.path else key


def _fmt_window(window: TimeWindow) -> str:
    return f"{window.start:%H:%M} bis {window.end:%H:%M}"


__all__ = [
    "DEVICE_COLUMN",
    "DEVICE_KINDS",
    "LOAD_COLUMN",
    "BaselineScheduleError",
    "Device",
    "DeviceKind",
    "ModulatingDevice",
    "ProgramDevice",
    "Task",
    "TimeWindow",
    "baseline_loads",
    "baseline_schedule",
    "devices_for",
    "parse_device",
    "parse_devices",
]
