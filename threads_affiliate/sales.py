"""ノウハウ5: 楽天セールの「前日」「開始日」「終了日」に投稿を集中させる."""
from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo


def _parse(value, tzinfo: ZoneInfo) -> dt.datetime:
    d = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
    return d if d.tzinfo else d.replace(tzinfo=tzinfo)


def key_days(sale: dict, tzinfo: ZoneInfo) -> dict[str, dt.date]:
    start, end = _parse(sale["start"], tzinfo), _parse(sale["end"], tzinfo)
    # 終了が深夜1:59のように日付をまたぐ場合、実質の最終日は前日
    last = (end - dt.timedelta(hours=3)).date()
    return {"前日": start.date() - dt.timedelta(days=1), "開始日": start.date(), "終了日": last}


def today_sale(sales: list[dict], today: dt.date, tzinfo: ZoneInfo) -> str | None:
    """今日がセールの重要日なら「楽天スーパーSALE（開始日）」のような文字列を返す."""
    for s in sales or []:
        for label, day in key_days(s, tzinfo).items():
            if day == today:
                return f"{s['name']}（{label}）"
    return None


def to_ics(sales: list[dict], tzinfo: ZoneInfo) -> str:
    """スマホのカレンダーに読み込めるiCalendarを作る（前日・開始日・終了日の朝9時に通知）."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//threads-affiliate//sale//JA",
             "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for s in sales or []:
        for label, day in key_days(s, tzinfo).items():
            ymd = day.strftime("%Y%m%d")
            uid = f"{ymd}-{hashlib.md5((s['name'] + label).encode()).hexdigest()[:10]}@threads-affiliate"
            lines += [
                "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{ymd}",
                f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:【{label}】{s['name']} アフィリ投稿を集中",
                "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT9H",
                f"DESCRIPTION:{s['name']} {label}", "END:VALARM", "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
