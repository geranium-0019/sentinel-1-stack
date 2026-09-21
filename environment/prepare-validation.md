# ISCE2 処理スクリプト生成の確認（2026-09-17）

`scripts/run.sh prepare CONFIG [--dry-run]` を追加。コンテナは network=none。
取得済み SLC、軌道 XML、WGS84 DEM の指定と bbox の包含を確認する。
軌道はローカル POEORB を優先し、なければ取得済み RESORB を使い、種別を表示する。
軌道のコピー・設定 YAML・入力リスト・コマンド・ログを生成先に保存する。
各生成は新しい scratch/stack_<UTC>_<ID> を使い、以前の生成先を上書きしない。

## Anak の確認結果

- work_dir: /mnt/ssd/sar-work/sentinel-1/anak
- bbox: [-6.27, -5.93, 105.25, 105.59]
- VV、IW3、基準日 20260729、range/azimuth looks 3/1、filter_strength 0.5、NESD
- 入力6日: 20260705, 20260717, 20260729, 20260810, 20260822, 20260903
- ISCE2 Sentinel1.parse による読み取り専用メタデータ確認で、全日がバースト5〜8。
- TOPSSwathSLCProduct.getCommonBurstLimits は基準日に対して全日 (0, 0, 4)。
- このネイティブメタデータ確認は今回の検証で実行したもの。prepare の汎用自動検査には未統合。
- POEORB 5件、RESORB 1件（9月3日）。新しい軌道のオンライン検索はしない。
- dry-run 成功。実生成成功、全6日が bbox 条件を通過、隣接5ペア。
- 生成先: scratch/stack_20260917T075017Z_d840f282
- run_01〜run_15_filter_coherence を PGV 実行対象として prepare.json に記録。
- run_16_unwrap も ISCE2 により生成されるが optional_unwrap_files に分けて記録。
- 16ファイルの bash -n 成功。5ペア分の filter/coherence 設定に 3/1 looks、0.5 が反映。
- fine.int、filt_fine.int、filt_fine.cor の出力指定を確認。実画像はまだ生成していない。
- prepare/launcher のテスト13件成功。過去の全体テスト不安定性は未解決。

## 制約と残作業

最初のネイティブ読み取りで8月22日の EOF の UTC 読み取りが AttributeError になった。
ホストで全 EOF の全 OSV に UTC が存在することを確認し、その後の読み取りと
全6日の共通バースト確認は成功。原因未特定であり、環境が安定したとの判断はしない。

prepare は生成のみ。処理実行・エラー時停止を保証するランナー、ジオコード、PGV は未実装。
上流 run_files はバックグラウンド実行と wait を含むため、単純な bash ループだけで
個々の処理失敗を確実に検出できるとは限らない。次工程で各終了コードを確認する。
PGV 用には wrapped phase/coherence を共通グリッドへ出力し、位相の循環性を保つ必要がある。

## ディレクトリ構成の変更

ユーザー指定により、Anak の生成先を `processing/` 直下に移行。
上記の日時付きパスは生成時の履歴。現在の run_files / configs / prepare.json は
`/mnt/ssd/sar-work/sentinel-1/anak/processing/` にある。
内部の `/work/...` パス、YAML スナップショット、設定 SHA256 も更新。
16件の bash 構文、記録内の実在パス、旧パスの残存がないことを確認。
今後の prepare は paths.processing 直下に生成し、空でない場合は上書きせず停止する。

## ログ保存先の統一

prepare.log と prepare.json は paths.logs 直下（既定: logs/）に変更。
Anak の既存ファイルも logs/ に移動し、記録内に保存先を追加。
processing/ は設定・処理ファイルのみを保持する。生成時の排他マーカーは .prepare.lock。
既存のログ・実行記録は上書きせずエラーとする。dry-run は書き込みなし。
