from typing import Any, Dict, Optional

def _as_list(v, default=None):
    if v is None:
        return default if default is not None else []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [v]

def snid_params_from_config(config: Optional[dict], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = ((config or {}).get("snid") or {}).get("defaults", {})
    def g(key, default):
        return cfg.get(key, default)

    default_usesub = [
        "Ia-norm", "Ic-norm", "Ib-norm", "Ia-91T", "Ia-91bg", "Gal",
        "IIn", "Ia-pec", "Ia-csm", "IIP", "LBV", "Ib-pec",
        "Ic-broad", "II-pec", "IIb", "IIL", "M-star", "AGN",
    ]

    params = {
        "wmin": float(g("wmin", 4000.0)),
        "wmax": float(g("wmax", 9000.0)),
        "zmin": float(g("zmin", 0.0)),
        "zmax": float(g("zmax", 1.2)),
        "emclip": g("emclip", None),
        "emwid": int(g("emwid", 40)),
        "agemin": int(g("agemin", -90)),
        "agemax": int(g("agemax", 1000)),
        "aband": bool(g("aband", False)),
        "use": _as_list(g("use", ["Ia", "Ib", "Ic", "II", "NotSN"]), default=["Ia", "Ib", "Ic", "II", "NotSN"]),
        "avoid": _as_list(g("avoid", []), default=[]),
        "avoidsub": _as_list(g("avoidsub", []), default=[]),
        "usesub": _as_list(g("usesub", default_usesub), default=default_usesub),
    }

    if overrides:
        merged = {**params, **overrides}
        # Re-normalize list-like fields in case overrides are str
        for key in ("use", "avoid", "avoidsub", "usesub"):
            merged[key] = _as_list(merged.get(key), default=params[key])
        # Coerce numeric types if overrides provided strings
        if "wmin" in merged: merged["wmin"] = float(merged["wmin"])
        if "wmax" in merged: merged["wmax"] = float(merged["wmax"])
        if "zmin" in merged: merged["zmin"] = float(merged["zmin"])
        if "zmax" in merged: merged["zmax"] = float(merged["zmax"])
        if "emwid" in merged: merged["emwid"] = int(merged["emwid"])
        if "agemin" in merged: merged["agemin"] = int(merged["agemin"])
        if "agemax" in merged: merged["agemax"] = int(merged["agemax"])
        if "aband" in merged: merged["aband"] = bool(merged["aband"])
        return merged

    return params