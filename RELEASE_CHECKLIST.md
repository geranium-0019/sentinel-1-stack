# Linux版の完成・配布チェック

**2026-09-21追記：rc1の最終反復検査は不合格です。全体テスト10回目で終了コード139（Segmentation fault）。配布可能とは判定していません。記録は `dist/0.1.0-rc1/` に保存しています。**

## 現在できること

- Dockerfile とローカル `sentinel-1-stack:dev` イメージがある。
- init → download → orbit → dem → prepare → run を実装済み。
- Anak の6観測・5干渉ペアを、NESD閾値0.7、3×1 looks、アンラップなしで実処理完了。
- export によるラップ位相・コヒーレンスの共通グリッドGeoTIFFと品質確認画像を実装。
- 標準はアンラップまで。省略は YAML の processing.unwrap: false 一行。
- アンラップは既定1ペアずつ。run --unwrap-jobsで実行時の上限を変更し、設定・生成ファイルを変えず再開可能。
- 2026-09-20: NumPy 1.26.4の通常起動対応候補で、キャッシュなし独立ビルド2回の構成一致と、各XML100回・全114テスト10回が成功。
- 同候補でISCE2/GDAL/ASFの共存、合成位相のSNAPHU、模擬ジョブのSIGINT中断・再開を確認。既存devの不規則な異常終了の根本原因は未確定で、通常のDockerfile/Composeへ構成を反映済み（正式公開前）。

## 正式版とする前に必要な作業

- [x] Anakの2観測・1ペア（3×1・9×3 looks）でprepare→run全16工程→exportを完走。SIGINT中断・再開と出力の位相/グリッド整合性を確認。検証範囲はこのケースに限定。
- [x] アンラップ位相・連結成分のGeoTIFF出力を追加（--include-unwrapped）。合成データと上記の実データで検証済み。
- [x] 検証候補で依存整合性・クリーンビルド・反復試験を確認。旧devの不規則な異常終了の根本原因は未確定で、検証結果は候補に限定。
- [x] 検証候補のベースdigest、Conda explicit lock、追加Python依存の版と独立2ビルドのインベントリ一致を記録。
- [x] 検証済み構成を通常のDockerfile/Composeへ反映。旧devを退避し、新devでXML100回・全114テスト10回とランチャーのdry-runが成功。Compose直接起動はWSLのshared mount設定が必要。
- [ ] 別のLinux環境でclone→build/pull→init→実行できることを確認する。
- [ ] READMEの利用手順、設定リファレンス、再開・設定変更時の退避手順を最終レビューする。
- [x] テスト・イメージビルドをGitHub Actionsに組み込む（main push / PR / 手動実行）。
- [ ] GitHubへの接続・push後、CIの初回成功とログ保存を確認する。
- [ ] ソースをGitで整理・コミットし、バージョン付きイメージをビルドする。
- [ ] 配布先を決め、承認済みの範囲でレジストリへ公開する。

`dev` イメージの作成と、正式版イメージの配布は別工程。
このチェックリストは従来TODOの履歴と、現在の実装・検証状況を区別するためのもの。
画像品質の確認は処理完走と別であり、少数のNESD有効画素・海域・DEMの観測時期は解析時に評価する。

詳細な合否・初回の検査側UIDエラーと修正記録は [安定化検証](environment/stabilization/REPORT.md) を参照。
比較用候補: `sentinel-1-stack:runtime-validation`。既定の `dev` は同構成を通常Dockerfileで再ビルドしたもの。
旧dev: `sentinel-1-stack:dev-before-runtime-20260920`。
