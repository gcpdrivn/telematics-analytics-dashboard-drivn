import datetime as dt
import os
import time

import pytest

from ingestion import api_pipeline


@pytest.fixture(params=["UTC", "Asia/Kolkata", "America/New_York"])
def host_tz(request):
    """The window must not depend on the host clock (Cloud Run runs UTC)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_window_starts_at_ist_midnight(host_tz):
    # 2026-09-28 00:00 IST == 2026-09-27 18:30 UTC
    expected = int(dt.datetime(2026, 9, 27, 18, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert api_pipeline._to_epoch_ms(dt.date(2026, 9, 28)) == expected
