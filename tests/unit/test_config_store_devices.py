"""Unit tests for device editing helpers in config_store (F4-04)."""

from pathlib import Path
from typing import Any

import pytest

from eeg_optimizer.config import ConfigError, config_to_dict, load_config
from eeg_optimizer.config_store import (
    BUILTIN_CONFIG_DIR,
    DEVICE_PRESETS,
    apply_edits,
    new_device,
    save_user_config,
    validate_devices,
)
from eeg_optimizer.devices import ProgramDevice, devices_for, parse_device

OFFLINE = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"


def _with_devices(index: int, devices: list[dict[str, Any]]) -> dict[str, Any]:
    raw = config_to_dict(load_config(OFFLINE))
    raw["households"][index]["devices"] = devices
    return {"households": raw["households"]}


@pytest.mark.parametrize("preset", list(DEVICE_PRESETS))
def test_every_preset_is_a_valid_device(preset: str) -> None:
    device = parse_device(new_device(preset, []), "devices[0]")
    assert device.id == preset


def test_new_device_picks_free_id() -> None:
    assert new_device("washing_machine", ["washing_machine"])["id"] == "washing_machine_2"
    taken = ["washing_machine", "washing_machine_2"]
    assert new_device("washing_machine", taken)["id"] == "washing_machine_3"


def test_new_device_returns_independent_copy() -> None:
    first = new_device("ev", [])
    first["window"]["start"] = "20:00"
    assert new_device("ev", [])["window"]["start"] == "18:00"


def test_new_device_rejects_unknown_preset() -> None:
    with pytest.raises(KeyError, match="unbekannte Gerätevorlage"):
        new_device("sauna", [])


def test_apply_edits_adds_washing_machine_and_roundtrips(tmp_path: Path) -> None:
    base = load_config(OFFLINE)
    devices = [
        *base.households[0].devices,
        new_device("washing_machine", ["ev", "washing_machine"]),
    ]
    edited = apply_edits(base, _with_devices(0, devices))
    saved = load_config(save_user_config(edited, "with_wm", user_dir=tmp_path))
    parsed = devices_for(saved)[saved.households[0].id]
    assert [d.id for d in parsed][-1] == "washing_machine_2"
    assert isinstance(parsed[-1], ProgramDevice)
    assert parsed[-1].energy_kwh_per_day == pytest.approx(1.0)


def test_apply_edits_removes_device() -> None:
    base = load_config(OFFLINE)
    edited = apply_edits(base, _with_devices(0, []))
    assert edited.households[0].devices == ()
    assert devices_for(edited)[edited.households[0].id] == ()


def test_apply_edits_rejects_unreachable_energy_in_german() -> None:
    ev = {**new_device("ev", []), "energy_kwh_per_day": 500.0}
    with pytest.raises(ConfigError) as info:
        apply_edits(load_config(OFFLINE), _with_devices(0, [ev]))
    message = str(info.value)
    assert message.startswith("households[0].devices[0].energy_kwh_per_day:")
    assert "nicht erreichbar" in message


def test_apply_edits_rejects_baseline_not_fitting_on_dst_day() -> None:
    # 4 h at full power from 23:00 fill the window 23:00 to 03:00 exactly; on 2023-03-26
    # the window is only 3 h long, so the baseline cannot be built.
    tight = {
        "id": "boiler",
        "kind": "modulating",
        "power_kw": 1.0,
        "energy_kwh_per_day": 4.0,
        "window": {"start": "23:00", "end": "03:00"},
        "baseline_start": "23:00",
    }
    with pytest.raises(ConfigError, match=r"households\[1\]\.devices\[0\]: .*Zeitumstellung"):
        apply_edits(load_config(OFFLINE), _with_devices(1, [tight]))


def test_apply_edits_rejects_duplicate_device_id() -> None:
    devices = [new_device("ev", []), new_device("ev", [])]
    with pytest.raises(ConfigError, match=r"households\[0\]\.devices\[1\]\.id: .*doppelt"):
        apply_edits(load_config(OFFLINE), _with_devices(0, devices))


def test_validate_devices_accepts_example_configs() -> None:
    validate_devices(load_config(OFFLINE))
    validate_devices(load_config(BUILTIN_CONFIG_DIR / "example_community.yaml"))
