from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


_aware = ensure_aware  # 하위 호환 별칭


def to_kst(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(KST)


def to_kst_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return to_kst(dt).isoformat(timespec="seconds")


def kst_date_label(dt: datetime) -> str:
    return to_kst(dt).strftime("%m-%d")


def kst_datetime_label(dt: datetime) -> str:
    return to_kst(dt).strftime("%m-%d %H:%M")


def kst_yyyymmdd(dt: datetime) -> str:
    return to_kst(dt).strftime("%Y%m%d")
