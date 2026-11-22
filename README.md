# jra-odds-collector

JRA開催日の単勝・複勝・ワイド・馬連・3連複の発走前オッズを、Yahoo!競馬から時系列で記録します。
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

上の間隔は単勝・複勝・ワイド。組合せが多い馬連（18頭で153通り）と3連複（同816通り）は、発走前の区切りごとに1回だけ取ります（区切りより前は朝の1回、発走後は取らない）。

| 券種 | 取る区切り（発走までの残り分） |
|---|---|
| 馬連 | 朝、90・60・30・15・10・5・2分以内 |
| 3連複 | 朝、30・10・4分以内 |

## CSVの列

`snapshot_at, race_date, track_id, race_number, yahoo_race_id, post_time, odds_status, odds_updated_at, bet_type, combination, horse_name, odds_low, odds_high`

単勝・馬連・3連複は `odds_low == odds_high`。`odds_status` が `確定オッズ` の行が最終オッズです。

## 手元で動かす

```
python -m collector.loop --race-date 2026-10-12 --store ./store --no-push --max-minutes 1
```

標準ライブラリのみで動きます（Python 3.10以上）。
