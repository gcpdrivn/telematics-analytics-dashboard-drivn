"""Phase 0 reconnaissance for the Fleetx API (see
docs/Fleetx_Drivn_API_Integration_Guide_1.pdf).

Standalone and read-only: logs in, then smoke-tests every documented
endpoint against live data and prints a coverage report. Not wired into
the ingestion pipeline -- this only exists to establish which endpoints
are usable and what their real response shapes look like, ahead of
building the parallel API ingestion pipeline.

Run: uv run python -m ingestion.api_explore
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from ingestion.fleetx_client import BASE_URL, REPO_ROOT, TIMEOUT, auth_headers, login


@dataclass
class ProbeResult:
    name: str
    method: str
    url: str
    ok: bool
    status_code: int | None
    detail: str
    sample: Any = None


def _probe(
    name: str,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> ProbeResult:
    start = time.monotonic()
    try:
        resp = requests.request(method, url, headers=headers, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return ProbeResult(name, method, url, False, None, f"request error: {exc}")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    content_type = resp.headers.get("content-type", "")

    if not resp.ok:
        return ProbeResult(
            name, method, url, False, resp.status_code, f"{elapsed_ms}ms: {resp.text[:300]}"
        )
    if "application/json" in content_type:
        body = resp.json()
        sample = body if isinstance(body, (list, dict)) else str(body)[:500]
    else:
        sample = f"<non-JSON, {len(resp.content)} bytes, content-type={content_type}>"
    return ProbeResult(name, method, url, True, resp.status_code, f"{elapsed_ms}ms", sample)


def _find_vehicles(realtime_sample: Any) -> list[dict[str, Any]]:
    if not isinstance(realtime_sample, dict):
        return []
    for key in ("vehicles", "data", "results"):
        value = realtime_sample.get(key)
        if isinstance(value, list):
            return value
    return []


def run() -> tuple[list[ProbeResult], Any, Any]:
    results: list[ProbeResult] = []

    token = login()
    results.append(ProbeResult("Login", "POST", f"{BASE_URL}/api/v1/login", True, 200, "ok"))
    headers = auth_headers(token)

    now_ms = int(time.time() * 1000)
    day_ago_ms = now_ms - 24 * 3600 * 1000

    # Realtime Analytics doubles as our source of real vehicleIds/plates --
    # the docs only show placeholder examples for the per-vehicle endpoints.
    realtime = _probe(
        "Realtime Analytics",
        "GET",
        f"{BASE_URL}/api/v1/analytics/live",
        headers,
        params={"mergeDevices": "true"},
    )
    results.append(realtime)

    vehicles = _find_vehicles(realtime.sample) if realtime.ok else []
    sample_vehicle = vehicles[0] if vehicles else {}
    sample_vehicle_id = sample_vehicle.get("vehicleId") or sample_vehicle.get("id")
    sample_plate = sample_vehicle.get("vehicleNumber")

    results.append(
        _probe(
            "Address Book",
            "GET",
            f"{BASE_URL}/api/v1/address_book/search",
            headers,
            params={"size": 5, "enabled": "true"},
        )
    )

    results.append(
        _probe(
            "Alarm (fleet-wide, 24h)",
            "GET",
            f"{BASE_URL}/api/v1/alarms/",
            headers,
            params={"from": day_ago_ms, "to": now_ms},
        )
    )

    per_vehicle_endpoints = [
        (
            "History Report",
            f"{BASE_URL}/api/v1/trips/",
            {"vehicleId": sample_vehicle_id, "from": day_ago_ms, "to": now_ms},
        ),
        (
            "Alarm Report",
            f"{BASE_URL}/api/v1/alarms/",
            {
                "vehicleId": sample_vehicle_id,
                "from": day_ago_ms,
                "to": now_ms,
                "fetchDetails": "true",
            },
        ),
        (
            "Asset Data (Excel export)",
            f"{BASE_URL}/api/v1/trips/route/points/export-excel",
            {"vehicleId": sample_vehicle_id, "from": day_ago_ms, "to": now_ms},
        ),
    ]
    for name, url, params in per_vehicle_endpoints:
        if sample_vehicle_id:
            results.append(_probe(name, "GET", url, headers, params=params))
        else:
            results.append(
                ProbeResult(
                    name,
                    "GET",
                    url,
                    False,
                    None,
                    "skipped: no vehicleId discovered from Realtime Analytics",
                )
            )

    # No job-listing endpoint is documented, so there's no reliable way to
    # source a real job ID -- left unprobed rather than guessing.
    results.append(
        ProbeResult(
            "Job Status",
            "GET",
            f"{BASE_URL}/api/v1/dispatch/{{id}}",
            False,
            None,
            "not probed: no job-listing endpoint documented to source a real job ID",
        )
    )

    return results, sample_vehicle_id, sample_plate


def main() -> None:
    results, sample_vehicle_id, sample_plate = run()

    print(f"\nFleetx API probe -- base URL {BASE_URL}")
    if sample_vehicle_id:
        print(f"Sample vehicle for per-vehicle endpoints: id={sample_vehicle_id} plate={sample_plate}\n")
    else:
        print("WARNING: could not discover a sample vehicleId; per-vehicle endpoints skipped.\n")

    print(f"{'Endpoint':28} {'Status':6} Detail")
    print("-" * 100)
    for r in results:
        print(f"{r.name:28} {'OK' if r.ok else 'FAIL':6} {r.detail}")

    report_dir = REPO_ROOT / "data" / "api_probe"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"fleetx_probe_{int(time.time())}.json"
    payload = [
        {
            "name": r.name,
            "method": r.method,
            "url": r.url,
            "ok": r.ok,
            "status_code": r.status_code,
            "detail": r.detail,
            "sample": r.sample,
        }
        for r in results
    ]
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nFull response samples written to {out_path}")

    # Job Status is deliberately unprobed, not failed -- don't count it.
    if any(not r.ok for r in results if r.name != "Job Status"):
        sys.exit(1)


if __name__ == "__main__":
    main()
