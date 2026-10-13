"""JRA開催日程とYahoo!競馬のオッズページを取得・解析する（標準ライブラリのみ）。

出力CSV（縦持ち）の列:
    snapshot_at, race_date, track_id, race_number, yahoo_race_id, post_time,
    odds_status, odds_updated_at, bet_type, combination, horse_name, odds_low, odds_high
単勝は odds_low == odds_high。
"""

from __future__ import annotations

import csv
import html
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

TRACK_CODE_MAP = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10",
}
FIELDNAMES = [
    "snapshot_at", "race_date", "track_id", "race_number", "yahoo_race_id", "post_time",
    "odds_status", "odds_updated_at", "bet_type", "combination", "horse_name", "odds_low", "odds_high",
]
FINAL_STATUS = "確定オッズ"
BASE_URL = "https://sports.yahoo.co.jp/keiba/race"
REQUEST_INTERVAL_SEC = 0.5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ScheduledRace:
    yahoo_race_id: str
    track_id: str
    race_number: int
    post_time: str  # HH:MM

    def post_at(self, race_date: date) -> datetime:
        hour, minute = (int(part) for part in self.post_time.split(":"))
        return datetime.combine(race_date, datetime.min.time()).replace(hour=hour, minute=minute)


@dataclass(frozen=True)
class OddsRecord:
    bet_type: str
    combination: str
    horse_name: str
    odds_low: float
    odds_high: float


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def fetch_with_retry(url: str, *, attempts: int = 3, backoff_sec: float = 2.0) -> str:
    """一時的な通信エラー（URLError・タイムアウト）に備えて再試行する。"""
    for attempt in range(1, attempts + 1):
        try:
            return fetch_html(url)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(backoff_sec * attempt)
    raise AssertionError("unreachable")


def parse_jra_meetings(html_text: str) -> list[dict[str, Any]]:
    seen: set[tuple[str, int, int]] = set()
    meetings: list[dict[str, Any]] = []
    for meeting_no, track_id, day_no in re.findall(r"(\d+)回([^\d<]+?)(\d+)日", html_text):
        track_id = track_id.strip()
        key = (track_id, int(meeting_no), int(day_no))
        if track_id not in TRACK_CODE_MAP or key in seen:
            continue
        seen.add(key)
        meetings.append({"meeting_no": int(meeting_no), "track_id": track_id, "day_no": int(day_no)})
    return meetings


def parse_race_schedule(list_html: str, *, meeting_id: str, track_id: str) -> list[ScheduledRace]:
    return [
        ScheduledRace(f"{meeting_id}{int(number):02d}", track_id, int(number), post_time.zfill(5))
        for number, post_time in re.findall(r"(\d{1,2})R\s*<p>\s*(\d{1,2}:\d{2})\s*</p>", list_html)
    ]


def discover_schedule(race_date: date) -> list[ScheduledRace]:
    """JRAの開催カレンダーから当日の開催を探し、Yahoo!のレース一覧で発走時刻を得る。

    非開催日はカレンダーが404になり、例外を送出する。
    """
    calendar_url = (
        f"https://www.jra.go.jp/keiba/calendar{race_date.year}/{race_date.year}/"
        f"{race_date.month}/{race_date.month:02d}{race_date.day:02d}.html"
    )
    races: list[ScheduledRace] = []
    for meeting in parse_jra_meetings(fetch_html(calendar_url)):
        meeting_id = (
            f"{str(race_date.year)[-2:]}{TRACK_CODE_MAP[meeting['track_id']]}"
            f"{meeting['meeting_no']:02d}{meeting['day_no']:02d}"
        )
        list_html = fetch_with_retry(f"{BASE_URL}/list/{meeting_id}")
        races.extend(parse_race_schedule(list_html, meeting_id=meeting_id, track_id=meeting["track_id"]))
    return races


def parse_odds_status(page_html: str) -> tuple[str, str]:
    status_match = re.search(r'hr-label--oddsStatus">\s*([^<]+?)\s*<', page_html)
    updated_match = re.search(r"(\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})\s*更新", page_html)
    return (
        status_match.group(1).strip() if status_match else "",
        updated_match.group(1) if updated_match else "",
    )


def _span_floats(cell_html: str) -> list[float]:
    values = []
    for text in re.findall(r"<span[^>]*>\s*([^<]*?)\s*</span>", cell_html):
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


def parse_win_place_odds(page_html: str) -> list[OddsRecord]:
    table_match = re.search(r'<tbody[^>]*id="raceodds_table"[^>]*>(.*?)</tbody>', page_html, re.S)
    if table_match is None:
        return []
    records: list[OddsRecord] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.S):
        numbers = re.findall(r'hr-tableValue__data--number">\s*(\d+)\s*</td>', row)
        name_match = re.search(r'hr-tableValue__data--horse">.*?>([^<]+)</a>', row, re.S)
        odds_cells = re.findall(r'hr-tableValue__data--odds">(.*?)</td>', row, re.S)
        if not numbers or len(odds_cells) < 2:
            continue
        post_position = numbers[-1]
        horse_name = html.unescape(name_match.group(1).strip()) if name_match else ""
        win_values = _span_floats(odds_cells[0])
        place_values = _span_floats(odds_cells[1])
        if len(win_values) == 1:
            records.append(OddsRecord("単勝", post_position, horse_name, win_values[0], win_values[0]))
        if len(place_values) == 2:
            records.append(OddsRecord("複勝", post_position, horse_name, place_values[0], place_values[1]))
    return records


def parse_wide_odds(page_html: str) -> list[OddsRecord]:
    records: list[OddsRecord] = []
    for table in re.findall(r"<table[^>]*hr-tableLeftTop--oddsW[^>]*>(.*?)</table>", page_html, re.S):
        first_match = re.search(r'<thead>.*?scope="col"[^>]*>\s*(\d+)\s*<', table, re.S)
        if first_match is None:
            continue
        first = int(first_match.group(1))
        for second, cell in re.findall(r'scope="row">\s*(\d+)\s*</th>\s*<td[^>]*>(.*?)</td>', table, re.S):
            values = _span_floats(cell)
            if len(values) == 2:
                pair = sorted((first, int(second)))
                records.append(OddsRecord("ワイド", f"{pair[0]}-{pair[1]}", "", values[0], values[1]))
    return records


PAGES = {"win_place": ("tfw", parse_win_place_odds), "wide": ("wide", parse_wide_odds)}


def snapshot_race(race: ScheduledRace, *, race_date: date, bet_types: list[str]) -> tuple[list[dict[str, object]], bool]:
    """1レース分のオッズを取得して行に変換する。2つ目の戻り値は確定オッズかどうか。"""
    snapshot_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, object]] = []
    finalized = False
    for bet_type in bet_types:
        page_code, parser_fn = PAGES[bet_type]
        try:
            page_html = fetch_with_retry(f"{BASE_URL}/odds/{page_code}/{race.yahoo_race_id}")
        except Exception as error:
            print(f"fetch_failed race={race.yahoo_race_id} page={page_code} error={type(error).__name__}", flush=True)
            continue
        time.sleep(REQUEST_INTERVAL_SEC)
        status, updated = parse_odds_status(page_html)
        if status == FINAL_STATUS and page_code == "tfw":
            finalized = True
        for record in parser_fn(page_html):
            rows.append({
                "snapshot_at": snapshot_at, "race_date": race_date.isoformat(), "track_id": race.track_id,
                "race_number": race.race_number, "yahoo_race_id": race.yahoo_race_id, "post_time": race.post_time,
                "odds_status": status, "odds_updated_at": updated, "bet_type": record.bet_type,
                "combination": record.combination, "horse_name": record.horse_name,
                "odds_low": record.odds_low, "odds_high": record.odds_high,
            })
    return rows, finalized


def append_rows(output: Path, rows: list[dict[str, object]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output.exists()
    with output.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
