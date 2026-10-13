"""開催日のオッズを、発走までの残り時間に応じた間隔で取り続ける。

GitHub Actions の定期実行は数時間遅れたり間引かれたりするため、1回の起動で長時間ループする。
ジョブの上限（6時間）が近づいたら終了し、残りがあればワークフロー側が後継のジョブを起動する。

    python -m collector.loop --store store --max-minutes 340
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from collector.odds import ScheduledRace, append_rows, discover_schedule, snapshot_race

# (発走までの残り分の上限, 取得間隔の分)。発走直前ほど細かく取る
INTERVALS = [(10, 1), (30, 5), (90, 10), (24 * 60, 30)]
AFTER_POST_INTERVAL_MIN = 5
GIVE_UP_AFTER_POST_MIN = 60  # 確定オッズが出ないまま発走からこの分を過ぎたら諦める
FIRST_CAPTURE_BEFORE_MIN = 150  # 最初のレースのこの分前から取り始める
PUSH_INTERVAL_SEC = 300
TICK_SEC = 15


def interval_minutes(minutes_to_post: float) -> float:
    if minutes_to_post <= 0:
        return AFTER_POST_INTERVAL_MIN
    for upper, interval in INTERVALS:
        if minutes_to_post <= upper:
            return interval
    return INTERVALS[-1][1]


def due_races(schedule: list[ScheduledRace], state: dict, *, race_date: date, now: datetime) -> list[ScheduledRace]:
    finalized = set(state["finalized"])
    due = []
    for race in schedule:
        if race.yahoo_race_id in finalized:
            continue
        minutes_to_post = (race.post_at(race_date) - now).total_seconds() / 60
        if minutes_to_post < -GIVE_UP_AFTER_POST_MIN:
            continue
        last = state["last_capture"].get(race.yahoo_race_id)
        # 間隔ちょうどだとティックのずれで1回飛ばすことがあるので少し早めに取る
        if last is None or (now - datetime.fromisoformat(last)).total_seconds() >= interval_minutes(minutes_to_post) * 60 - TICK_SEC:
            due.append((minutes_to_post, race))
    return [race for _, race in sorted(due, key=lambda item: abs(item[0]))]


def remaining(schedule: list[ScheduledRace], state: dict, *, race_date: date, now: datetime) -> list[ScheduledRace]:
    finalized = set(state["finalized"])
    limit = timedelta(minutes=GIVE_UP_AFTER_POST_MIN)
    return [r for r in schedule if r.yahoo_race_id not in finalized and now <= r.post_at(race_date) + limit]


def git_push(store: Path, message: str) -> None:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(store), *args], capture_output=True, text=True)

    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("-c", "user.name=odds-collector", "-c", "user.email=odds-collector@users.noreply.github.com",
        "commit", "--quiet", "-m", message)
    branch = os.environ.get("DATA_BRANCH", "data")
    for _ in range(3):
        if git("push", "--quiet", "origin", f"HEAD:{branch}").returncode == 0:
            return
        git("pull", "--quiet", "--rebase", "origin", branch)
    print("push_failed", flush=True)


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="開催日のオッズを発走時刻に合わせて取り続ける")
    parser.add_argument("--race-date", default="", help="YYYY-MM-DD（省略時は今日）")
    parser.add_argument("--store", default="store", help="データブランチを置いたディレクトリ")
    parser.add_argument("--bet-types", default="win_place,wide")
    parser.add_argument("--max-minutes", type=float, default=340, help="この分数で打ち切る（ジョブ上限6時間より短く）")
    parser.add_argument("--no-push", action="store_true", help="git に保存しない（手元での確認用）")
    args = parser.parse_args()
    deadline = time.monotonic() + args.max_minutes * 60

    race_date = date.fromisoformat(args.race_date) if args.race_date else date.today()
    bet_types = [value.strip() for value in args.bet_types.split(",") if value.strip()]
    store = Path(args.store)
    day_dir = store / "odds" / f"{race_date:%Y}"
    output = day_dir / f"odds_snapshots_{race_date:%Y%m%d}.csv"
    state_path = day_dir / f"state_{race_date:%Y%m%d}.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    if "schedule" not in state:
        try:
            schedule = discover_schedule(race_date)
        except Exception as error:
            print(f"race_date={race_date} note=開催日程を取得できず({type(error).__name__})", flush=True)
            schedule = []
        if not schedule:
            print(f"race_date={race_date} note=開催なし", flush=True)
            write_output("more", "false")
            return
        state = {"schedule": [race.__dict__ for race in schedule], "finalized": [], "last_capture": {}}
        day_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    state.setdefault("last_capture", {})
    schedule = [ScheduledRace(**item) for item in state["schedule"]]
    print(f"race_date={race_date} races={len(schedule)} finalized={len(state['finalized'])}", flush=True)

    first_post = min(race.post_at(race_date) for race in schedule)
    start_at = first_post - timedelta(minutes=FIRST_CAPTURE_BEFORE_MIN)
    last_push = time.monotonic()
    while True:
        now = datetime.now()
        if not remaining(schedule, state, race_date=race_date, now=now):
            break
        if time.monotonic() > deadline:
            break
        if now < start_at:
            time.sleep(min(60, (start_at - now).total_seconds()))
            continue
        for race in due_races(schedule, state, race_date=race_date, now=now):
            if time.monotonic() > deadline:
                break
            rows, finalized = snapshot_race(race, race_date=race_date, bet_types=bet_types)
            append_rows(output, rows)
            state["last_capture"][race.yahoo_race_id] = datetime.now().isoformat(timespec="seconds")
            if finalized:
                state["finalized"] = sorted(set(state["finalized"]) | {race.yahoo_race_id})
            minutes_to_post = (race.post_at(race_date) - now).total_seconds() / 60
            print(f"{now:%H:%M:%S} {race.track_id}{race.race_number}R post={race.post_time} "
                  f"mtp={minutes_to_post:.0f} rows={len(rows)} final={finalized}", flush=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        if not args.no_push and time.monotonic() - last_push >= PUSH_INTERVAL_SEC:
            git_push(store, f"odds {race_date} {datetime.now():%H:%M}")
            last_push = time.monotonic()
        time.sleep(TICK_SEC)

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.no_push:
        git_push(store, f"odds {race_date} {datetime.now():%H:%M}")
    left = remaining(schedule, state, race_date=race_date, now=datetime.now())
    print(f"done finalized={len(state['finalized'])}/{len(schedule)} remaining={len(left)}", flush=True)
    write_output("more", "true" if left else "false")


if __name__ == "__main__":
    main()
