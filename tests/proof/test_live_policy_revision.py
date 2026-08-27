"""Proof that every versioned source surface describes NYC's live risk posture."""

from __future__ import annotations

import re
from pathlib import Path

from config import CITY_BLACKLIST_POLICY_REVISION, DEFAULT_CITY_BLACKLIST


ROOT = Path(__file__).resolve().parents[2]

LIVE_POLICY = {
    "KALSHI_MAX_DEPLOYED_PCT": "0.90",
    "KALSHI_MAX_CONCURRENT_POSITIONS": "20",
    "KALSHI_SAME_EVENT_FAMILY_CAP": "5",
    "KALSHI_HUB_EXPOSURE_PCT": "0.40",
    "KALSHI_HUB_EXPOSURE_MIN_USD": "20",
    "KALSHI_MAX_QTY_PER_POSITION": "15",
    "KALSHI_MAX_USD_PER_POSITION": "10.0",
    "KALSHI_MIN_ENTRY_PRICE": "0.34",
    "KALSHI_KELLY_CAP": "0.12",
    "KALSHI_KELLY_FRACTION": "0.25",
    "KALSHI_MAX_RISK_PER_EVENT_PCT": "0.08",
}


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_config_fallbacks_match_versioned_live_policy():
    source = (ROOT / "config.py").read_text(encoding="utf-8")
    for key, value in LIVE_POLICY.items():
        pattern = rf'os\.getenv\(\s*"{re.escape(key)}"\s*,\s*"{re.escape(value)}"\s*\)'
        assert re.search(pattern, source), f"config fallback drifted for {key}"


def test_examples_and_release_gates_pin_the_same_live_policy():
    paths = (
        ROOT / ".env.example",
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "deploy-nyc.yml",
    )
    expected_blacklist = ",".join(sorted(DEFAULT_CITY_BLACKLIST))

    for path in paths:
        values = _env_values(path)
        for key, value in LIVE_POLICY.items():
            assert values.get(key) == value, f"{path.name} drifted for {key}"
        assert values.get("CITY_BLACKLIST") == expected_blacklist


def test_blacklist_policy_has_an_explicit_revision():
    assert CITY_BLACKLIST_POLICY_REVISION == "2026-08-26.top15-hubs"


def test_deploy_migrates_the_persisted_droplet_env_to_live_policy():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    for key, value in LIVE_POLICY.items():
        assert f'"{key}": "{value}"' in deploy
    assert '"RBI_MIN_DAYS": "7"' in deploy
    assert '"RBI_MIN_NEW_CLEAN_TRADES": "24"' in deploy
    assert '"KALSHI_MAKER_FEE_RATE",' in deploy
    assert '"OPEN_METEO_API_KEY",' in deploy
