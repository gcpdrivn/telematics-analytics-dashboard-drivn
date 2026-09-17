"""Minimal Fleetx API client (see docs/Fleetx_Drivn_API_Integration_Guide_1.pdf,
kept locally, gitignored -- it carries live credentials).

Just the pieces the ingestion pipeline needs: login and the History Report
(per-vehicle trip data) endpoint. ingestion/api_explore.py's broader
endpoint probe imports login()/headers from here too, so there's one place
that knows how to authenticate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

BASE_URL = os.environ.get("FLEETX_BASE_URL", "https://api.fleetx.io")
CLIENT_ID = os.environ.get("FLEETX_CLIENT_ID", "fleetxweb")
TIMEOUT = 30


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Missing {name}. Set FLEETX_USERNAME / FLEETX_PASSWORD in .env "
            f"(see .env.example) -- do not hardcode credentials."
        )
    return value


def login() -> str:
    username = _required_env("FLEETX_USERNAME")
    password = _required_env("FLEETX_PASSWORD")
    resp = requests.post(
        f"{BASE_URL}/api/v1/login",
        data={"username": username, "password": password, "grant_type": "password"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "clientid": CLIENT_ID,
        "accept": "application/json, text/plain, */*",
    }


def get_trips(
    token: str, vehicle_id: int, from_ms: int, to_ms: int
) -> list[dict[str, Any]]:
    """History Report API -- per-trip records for one vehicle over
    [from_ms, to_ms] (epoch milliseconds). Raises on a non-2xx response;
    callers decide whether that fails the whole run or just this vehicle."""
    resp = requests.get(
        f"{BASE_URL}/api/v1/trips/",
        headers=auth_headers(token),
        params={"vehicleId": vehicle_id, "from": from_ms, "to": to_ms, "skipList": "false"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("trips", [])
