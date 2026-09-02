from datetime import datetime, timezone

from app.clock import kst_date_label, kst_datetime_label, now_utc, to_kst_iso
from app.ids import new_id


def test_new_id_is_ulid_26_chars():
    a, b = new_id(), new_id()
    assert len(a) == 26 and a != b


def test_to_kst_iso_converts_utc():
    dt = datetime(2026, 8, 22, 9, 11, 4, tzinfo=timezone.utc)
    assert to_kst_iso(dt) == "2026-08-22T18:11:04+09:00"


def test_to_kst_iso_treats_naive_as_utc():
    dt = datetime(2026, 8, 22, 9, 11, 4)
    assert to_kst_iso(dt) == "2026-08-22T18:11:04+09:00"


def test_to_kst_iso_none():
    assert to_kst_iso(None) is None


def test_labels():
    dt = datetime(2026, 8, 25, 5, 32, tzinfo=timezone.utc)
    assert kst_date_label(dt) == "08-25"
    assert kst_datetime_label(dt) == "08-25 14:32"


def test_now_utc_is_aware():
    assert now_utc().tzinfo is not None
