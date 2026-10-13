# jra-odds-collector

JRA開催日の単勝・複勝・ワイドの発走前オッズを、Yahoo!競馬から時系列で記録します。
GitHub Actions で動き、データは `data` ブランチの `odds/YYYY/odds_snapshots_YYYYMMDD.csv` に保存されます。

## 取得間隔

| 発走までの残り | 間隔 |
|---|---|
| 90分超 | 30分 |
| 30〜90分 | 10分 |
| 10〜30分 | 5分 |
| 10分以内 | 1分 |
| 発走後（確定オッズが出るまで） | 5分 |

最初のレースの150分前から取り始め、全レースの確定オッズを取ったら終了します。

## CSVの列

`snapshot_at, race_date, track_id, race_number, yahoo_race_id, post_time, odds_status, odds_updated_at, bet_type, combination, horse_name, odds_low, odds_high`

単勝は `odds_low == odds_high`。`odds_status` が `確定オッズ` の行が最終オッズです。

## 手元で動かす

```
python -m collector.loop --race-date 2026-10-12 --store ./store --no-push --max-minutes 1
```

標準ライブラリのみで動きます（Python 3.10以上）。
