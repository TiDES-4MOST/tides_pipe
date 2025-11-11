from __future__ import annotations
import os

def _data_paths(config: dict) -> dict:
    return (config.get("data_paths") or {})

def spectra_night_dir(config: dict, night: str | None, ensure: bool = True) -> str:
    base = _data_paths(config).get("spectra_dir", "/data/spectra")
    d = os.path.join(base, str(night)) if night else base
    if ensure:
        os.makedirs(d, exist_ok=True)
    return d

def spectrum_path(config: dict, night: str | None, tides_id: str | int, ensure_dir: bool = True) -> str:
    d = spectra_night_dir(config, night, ensure=ensure_dir)
    return os.path.join(d, f"{tides_id}_spectrum.txt")