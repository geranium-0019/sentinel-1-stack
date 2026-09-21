# 処理ランナー（2026-09-17）

標準はアンラップまで。例外はプロジェクト YAML の processing.unwrap: false 一行のみ。
今回は Anak で false を指定し、run_01〜run_15 を実行。
run は生成された単純なコマンド・末尾 &・wait を読み、各プロセスの終了コードを検証する。
末尾に wait がなくても全子プロセスを待つ。失敗時は同時実行中のプロセスグループを終了。
--resume は成功済み工程をスキップする。失敗工程は先頭から再実行。
設定・生成スクリプトの指紋が異なると再開を拒否する（unwrap 切替は指紋に含めない）。

ログは paths.logs 以下。run.json は工程・試行回数・各ログ・状態を保存。
ISCE2 が CWD に書く isce.log は processing/isce.log から logs/isce.log へのリンクで集約。
prepare.json の過去の PGV 項目は実行選択に使わず、現在の YAML を参照する。

最初の runner/prepare/launcher 関連テスト19件成功。
実プロジェクトの dry-run 成功、15工程の実処理を開始。

再開時の成功工程スキップ、後からのアンラップ追加、ISCEログの集約を含む
runner/prepare/launcher の21テストが成功。

## 実処理の結果

2026-09-17 08:05 UTC に開始。run_01〜run_06 は終了コード0で完了。
run_07_pairs_misreg で 20260717_20260822 の estimateAzimuthMisreg が
「Coherence threshold too strict. No points left for reliable ESD estimate」で終了1。
ランナーは同時実行中の組を終了し、run_08以降を実行せず停止した。
これは今回のログ上では明示的なNESDデータ条件のエラーであり、以前の不規則なPythonエラーと混同しない。

logs/nesd_diagnostics.json に保存した処理済み6ペアの閾値超過画素数:
0.85で 1, 10, 4, 40, 12, 0。失敗ペアは最大コヒーレンス0.8023、0.8超は1画素、0.7超は38画素。
後半ペアは停止のため未処理であり、この診断は全12ペアを網羅しない。
閾値やcoregistration方式は自動変更していない。解析範囲・NESDの有効画素分布を再検討する必要がある。
アンラップ・最終干渉画像生成は未完了。全処理の完了とは扱わない。

## 設定と日本語のエラー対処案内

ESDコヒーレンス閾値・レンジ位置ずれSNR閾値・NESD接続数・仮想結合・rmFilterを
YAMLから生成引数へ反映する。省略時は以前のISCE2既定値を維持。
追加キーが既定値の場合、既存run.jsonの指紋と一致することを実プロジェクトの読み取り専用検証で確認。
実際の失敗ログからNESDエラーを認識して日本語の対処手順が表示されることも確認。
処理の再実行や、Anakの閾値変更はしていない。
