"""
Default parameters for the NGSF classifier API.

Parameter names match the ``Params`` Pydantic model in
``ngsf_docker/ngsf_api.py`` exactly.  All values can be overridden via the
``config["ngsf"]["defaults"]`` block in the pipeline config YAML/dict.
"""
from typing import Any, Dict, Optional


def ngsf_params_from_config(
    config: Optional[dict],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a dict of NGSF API parameters, sourced from *config* with
    sensible defaults.  Any key present in *overrides* wins over both the
    config value and the built-in default.

    The returned dict does **not** include ``file``; the handler adds that
    field from the spectrum path.
    """
    cfg = ((config or {}).get("ngsf") or {}).get("defaults", {})

    def g(key, default):
        return cfg.get(key, default)

    params: Dict[str, Any] = {
        # Redshift search window
        "z":            float(g("z",            0.0)),
        "z_min":        float(g("z_min",        0.0)),
        "z_max":        float(g("z_max",        0.1)),
        "z_int":        float(g("z_int",        0.01)),
        # Wavelength window (0.0 = use full range)
        "lower_lam":    float(g("lower_lam",    0.0)),
        "upper_lam":    float(g("upper_lam",    0.0)),
        # Epoch (phase) search window in days
        "epoch_low":    float(g("epoch_low",    0.0)),
        "epoch_high":   float(g("epoch_high",   0.0)),
        # Extinction search window
        "alam_low":     float(g("alam_low",    -2.0)),
        "alam_high":    float(g("alam_high",    2.0)),
        "alam_interval": float(g("alam_interval", 0.2)),
        # Spectral resolution (Å)
        "resolution":   float(g("resolution",   10.0)),
        # Masking flags (map to form's Telluric / Galaxy checkboxes)
        "mask_telluric": bool(g("mask_telluric", True)),
        "mask_galaxy":   bool(g("mask_galaxy",   True)),
    }

    if overrides:
        params.update(overrides)
        # Re-coerce numeric types in case overrides arrived as strings
        for fkey in ("z", "z_min", "z_max", "z_int", "lower_lam", "upper_lam",
                     "epoch_low", "epoch_high", "alam_low", "alam_high",
                     "alam_interval", "resolution"):
            if fkey in params:
                params[fkey] = float(params[fkey])
        for bkey in ("mask_telluric", "mask_galaxy"):
            if bkey in params:
                val = params[bkey]
                if isinstance(val, str):
                    params[bkey] = val.lower() not in ("0", "false", "no", "")
                else:
                    params[bkey] = bool(val)

    return params
