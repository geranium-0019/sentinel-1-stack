# 安定化の候補比較（2026-09-20）

NumPyは全候補で1.26.4を維持。実データは変更していない。
通常構成への反映を完了（末尾参照）。以下のdev未変更の記述は各比較時点の履歴。正式公開は未実施。

現在: 通常起動対応候補cと独立ビルド2回で、各XML100回・全114テスト10回が成功。
独立ビルドのインベントリも一致。別work_dirのAnak 2観測・1ペア（3×1 looks）は中断再開を経て、
全16工程・5種GeoTIFF出力・位相/グリッド整合性検査まで成功した。
検査側のUID指定漏れによる初回読み取り失敗と、その修正後の結果は両方保存。
標準9×3 looksでも中断再開・全16工程・出力検査まで成功。
候補の別名は `sentinel-1-stack:runtime-validation`（Python3.11.10、NumPy1.26.4）。

## 実施した比較

| 候補 | 結果 | 判定 |
|---|---|---|
| devのPython3.11.16 → 3.11.14と必要なlibffi/libglib変更 | 全114テスト1回成功 | これだけでは採用不可 |
| 上記 + imagecodecs2024.12.30 / tifffile2024.12.12等の依存整合 | pip check成功、XML100/100成功、全体テスト初回segfault | 不合格 |
| 同じ依存を空のprefixから作成し、ISCE2も再ビルド | pip check成功、全体114テスト中XML生成1件TypeError | 不合格 |
| 上記にPYTHONMALLOC=debug | segfault | 不合格 |
| 上記のregexだけ2024.11.6へ変更 | XML生成TypeError | 不合格 |
| Python3.11.10比較用候補 | 全114テスト10回連続成功。ただしXML100回試験の83回目にTypeError | 不合格 |

3.11.10の失敗はxml-082.log。`key.startswith('V')`で`TypeError: 'str' object is not callable`。
少数回の成功や全体テストだけでは見逃す問題が、決めた100回試験で検出できた。
Python3.11.14/3.11.10への変更だけでは解決していない。ASF、Python、コンパイラ、ネイティブ依存、実行ホストのどれが根本原因かは未確定。

## 検証の再実行

`scripts/validate_stability.py` を追加。対象イメージをIDへ解決してから、
ネットワークなし・プロジェクト読み取り専用・各試験は新しいコンテナで実行。
pip check、XML再現100回、全体テスト10回。最初の失敗で停止し、成功するまでやり直して合格扱いにしない。
faulthandlerと外部timeoutで停止も失敗として扱う。試験コンテナだけを識別名で後片付けする。
各ログとresult.jsonに判定を保存する。

```
python3 scripts/validate_stability.py --image sentinel-1-stack:stability-candidate \
  --output /tmp/sentinel-acceptance-new
```

## 固定とビルド

- candidate-linux-64.explicit.txt: 新規prefix用の版・build・checksum固定（不合格候補の再現用）。
- Dockerfile.clean: digest固定のmicromamba、上記explicit lockで空の環境を作成しISCE2を再ビルド。
- Python3.11.14以前はPythonパッケージがlibpythonも所有するため、3.11.16の別libpythonパッケージはlockから除外。
- explicitファイルを既存prefixへ重ねる初期実験は旧metadataが残り、破棄。rejected-overlayログは失敗過程の記録であり採用候補ではない。
- Python3.11.10も切り分け専用。不合格のため本採用しない。古い版を安定版として推奨するものではない。
- 本番Dockerfile・environment.yml・devタグを未変更。比較用Dockerfileとイメージは別管理。

## 当初未達だった基準（後続の結果は末尾）

1. 通常の起動経路を含む採用候補でXML100回かつ全体10回を無失敗で通す（比較専用の公式Python3.11.10では達成）。
2. 採用候補の独立した再ビルド2回とインベントリ一致。
3. アンラップを含む実データ完走・出力検証・中断再開検証。

今回の依存警告解消と再ビルド可能化は進展だが、上記を満たした安定版ができたという意味ではない。

## 公式Pythonによる追加比較

- 最小の公式Python3.11.10 + ASF関連環境でXML100回は成功（official-repeat/result.json）。
- 同じCondaの数値・地理ライブラリを、公式Pythonの実行ファイル・標準ライブラリで呼び出す比較用イメージを作成。
- `/usr/local/bin/python` を明示し、LD_LIBRARY_PATHで共有ライブラリの場所を指定する。この構成は原因確認用であり、通常の `scripts/run.sh --image ...` での運用設定にはまだしていない。
- 公式Python3.11.16（GCC14.2）も比較した。元のConda Python3.11.16はGCC15.3。版番号だけでなく、ビルドとリンク方法等が異なる。
- ビルド情報をconda-python-build-flags.json / official-python-build-flags.jsonに記録。
- 公式Python3.11.16 + 同じ数値・地理ライブラリで全114テストの初回、ISCE2/ASF/GDALの相互importとネイティブ操作は成功。

### 反復試験の最終結果

| 比較環境（NumPyはいずれも1.26.4） | pip check | XML（別プロセス100回） | 全114テスト（別コンテナ10回） |
|---|---|---|---|
| 公式Python3.11.10 + Conda数値・地理ライブラリ | 成功 | 100/100成功 | 10/10成功 |
| 公式Python3.11.16 + 同じ数値・地理ライブラリ | 成功 | 100/100成功 | 初回segfault、以降停止 |

- 3.11.10: `acceptance-official-runtime/result.json`、イメージID `sha256:1bfeffabe7a666df9217ce9f07573324aa124010ed962e1356f31756f53a5dbd`。
- 3.11.16: `acceptance-official-current/result.json`、イメージID `sha256:90116f84df8d4aedbebed17a6f890e879d6de3952744d755b6eec6258bb43bc7`。
- 3.11.16の失敗は `suite-00.log`。軌道ファイルのテストでElementTreeのXMLシリアライズ中に終了コード139。初回の単発成功を安定性の根拠にしない。
- いずれも明示的な `/usr/local/bin/python` による比較。通常のランチャーが選ぶConda Pythonの合格を意味しない。
- 成功した3.11.10環境も、根本原因の解消や実運用の安定性を証明したものではない。単に公式Pythonへ置き換えれば直る、GCCが原因、NumPyが原因などの断定はできない。
- 試験終了後、実行中のDockerコンテナなし。devのIDは `sha256:f4a3e67289ea538ed07024d4d0d12e320dfae2ee8acfe121aab856f33cb3b451` のまま。

### この比較時点での次作業（後続の結果は末尾）

1. 成功した比較環境を再現できる構成を整理し、通常ランチャーとISCE2が起動するPythonを統一した別候補を作る。
2. その候補で依存整合・XML100回・全体10回を再検証する。古いPythonの比較成功だけを根拠に本採用しない。
3. 独立ビルド2回の一致、アンラップを含む実データと中断再開の確認を行う。

本番Dockerfile・environment.yml・devタグの変更、安定版の公開は行っていない。


## 通常起動経路への組み込み（続き）

`Dockerfile.runtime-candidate` を追加。ベースdigest・Conda explicit lock・ISCE2 commitを固定し、
通常のPATHでも公式Python3.11.10を使用する検証専用構成。Micromambaの起動時activationを外し、
`python` / `python3` / ISCE2のenv-python shebangを同じ実行系へ向ける。
本番Dockerfileやdevタグは変更していない。

最初のruntime-candidate-aは、wheel取得ステージからPython全体をコピーしたため、
その工程で生成されたargparseのpycを含んだ。通常ランチャーとISCE2のhelpで
`ValueError: bad marshal data (digit out of range in long)` が発生し、不採用。
そのpycのSHA256は `7dd45f0e511dd839a81bfa500deff9d390d1c5ce30b093a5e66b7e914b30d131`。
元の公式イメージにはこのpycがなく、そこでargparseは読み込めた。
反復試験は中断として保存し、合格扱いにしていない（acceptance-runtime-launcher）。
これは既存のsegfault全体の原因を確定したものではない。

修正版runtime-candidate-bでは、wheel取得とは別の、未加工の公式Pythonステージからコピーする。
Anakのprepare --dry-run（読み取り専用マウント）で6観測・9ペアと
`/usr/local/bin/python`を使う生成コマンドを確認した。既存の処理・出力には書き込んでいない。
`check_runtime.py`は既定Python、子プロセスのPython、ISCE2の3コマンドのhelpを確認する。
`inventory.py`はPython実行系・Condaの版/build/checksum・Python配布物・ISCE2 commitを記録する。

runtime-candidate-bのXML100回は成功したが、全体試験初回でGDAL/PROJ関連3件が失敗。
Micromamba activationを省いたことでPROJ/GDALのデータパス設定が欠落したため。
この構成を独立ビルドしたruntime-rebuild-1/2のインベントリは一致したものの、
起動構成が不適切なため反復試験は中断し、不採用とした。

修正版runtime-candidate-cではMicromambaの既存entrypointを通した後に
`runtime-entrypoint.sh`で`/usr/local/bin`をPATH先頭に戻す。
これによりGDAL/PROJなどのactivation設定と、公式Pythonによる既定起動を両立する。
全114テストの事前確認は成功。一般ユーザー起動・Anak dry-run・反復試験は別ログに記録する。

`check_resume.py`で、実子プロセスを起動する模擬ジョブをSIGINT中断し、子プロセスの消滅、
interrupted記録、再開時の完了済み工程の省略、未完了工程の再実行と完了記録を確認した。
これはジョブ制御の検証であり、SAR実データの中断再開検証の代わりにはしない。

### 通常起動・独立ビルドの結果

以下の3イメージすべてで、既定の`python`によりpip check、XML100/100回、全114テスト10/10回が成功。

| 候補 | イメージID | 記録 |
|---|---|---|
| runtime-candidate-c | bb685946c2f2eb9a163cec09df948dc0c5c25e879598c1730da4b4b226f709e7 | acceptance-runtime-c/result.json |
| runtime-c-rebuild-1（--no-cache） | 6fba60f9eb9fe8c2ec9b99f5848d110ad9b21888f5f3b4ac9566140eac2459a1 | acceptance-runtime-c-rebuild-1/result.json |
| runtime-c-rebuild-2（--no-cache） | 5108c45b7571294dc47a39b5644a73b311576ae1f1c9cefe3328da13a50aa9b9 | acceptance-runtime-c-rebuild-2/result.json |

独立ビルド2回のインベントリは完全一致（runtime-c-rebuild-{1,2}-inventory.json）。
これはパッケージ構成の一致であり、Dockerレイヤーのバイト単位一致ではない。
両方で一般ユーザーによる既定Python・ISCE2 entry point、ASF/GDAL/ISCE2の相互importとネイティブ演算、
既知位相128×128のSNAPHU処理、模擬ジョブの実子プロセスSIGINT中断・再開も成功。
SNAPHU出力は定数オフセットを除いて誤差0.001rad未満、連結成分は全画素有効。
元の`tests/check_environment.py`による既存配布物の版不変検査もrebuild-2で成功。

### 実データ検証（進行中）

- 別work_dir: `/mnt/ssd/sar-work/sentinel-1/validation-runtime-20260920`
- Anakから20260717・20260729の2観測をコピー。元のAnakは変更しない。
- bbox、IW3、VV、NESD閾値0.7、3×1 looks等は元設定を使用。
- connections=1、num_processes=1、num_processes_topo=1、unwrap=true。
- prepare成功、16工程を生成。rebuild-2のイメージIDを指定してrunを開始。
- 完走・中断再開・出力検証の最終結果はまだ未判定。

実データrunは工程1〜5の完了後、工程6の途中で検証用コンテナにのみSIGINTを送信。
終了コード130、run.jsonのinterrupted状態、コンテナの終了を確認した。
設定・生成ファイルを変更せず同じイメージIDで`--resume --unwrap-jobs 1`を実行。
中断前後の状態をreal-state-before-interrupt.json / real-state-interrupted.jsonに保存。

実データのアンラップ入力は8142×5538（45,090,396画素）。
読み取り専用検査で複素干渉画像・コヒーレンスの非有限値は0、コヒーレンス範囲は0〜1。
コヒーレンス0.7以上は205,406画素（約0.46%）、振幅0は1,991,900画素。
これはジオコード前の結合画像全域の集計であり、指定bboxや火山周辺だけの品質評価ではない。
結果はreal-input-quality.json。SNAPHUのMCF初期解計算が長時間継続しており、
観測時のメモリは約18GiB、CPUは約1コア分。完了前に安定版・実データ合格とは判定しない。

### 標準9×3 looksの追加検証

3×1 looksのMCF計算が1時間を超えて継続しているため、標準9×3 looksを
`/mnt/ssd/sar-work/sentinel-1/validation-standard-20260920` で追加検証する。
元の3×1ジョブは停止・設定変更せず、9×3の成功で3×1を合格扱いに置き換えない。
入力2観測、bbox、NESD等は同じ。プロセス数とアンラップ同時実行数は1。
独立した入力コピーからprepareで16工程を生成し、runを開始した。
記録先はstandard-real/。完了後は同じ出力整合性基準で判定する。

固定したimagecodecs2024.12.30も、NumPy1.26.4でzlib/lzma/zstd/lzwの
TIFF roundtripを直接実行し、全成功（runtime-c-codec.log）。

SNAPHU公式説明では実行時間は干渉画像の難しさに依存する。
https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/
これは現在の処理が必ず終了する保証や、実行中の1.4.2の所要時間予測ではない。

通常ランチャーでASF軌道APIへも実接続を確認した（RES_OPTIONS=edns0）。
存在しない別の出力先を指定したorbit --dry-runにより、既存EOFの再利用を避けて
2観測のPOEORBをリモート問い合わせで解決。終了コード0、出力先は作成されていない。
記録: runtime-c-orbit-network.log。SLC本体取得・Earthdata認証の実試験ではない。


### 3×1 looksの最終結果

- 全16工程が終了コード0で完了。SNAPHUの計算時間は81分46.6秒。
- 完了済み工程のattempt数が変わらず、中断工程を再実行したこと、fingerprint不変を確認。
- export --include-unwrappedが終了コード0で完了し、5種のGeoTIFFを生成。
- 最初の出力検査は、検査コンテナのUID指定漏れで失敗した。出力ディレクトリは700、所有者1000:1000。
  通常ランチャーはホストUID/GIDを指定するが、検査だけ既定の別ユーザーで起動していた。
- 検査側もホストUID/GIDを指定して読み取り専用で実行し、成功。SARデータや検査の閾値・条件は変更していない。
  初回失敗はreal-finalization.json / real-output-check.json、修正後はreal-output-check-user.jsonに保存。
  finalize_real_validation.pyもUID/GIDを引き継ぐよう修正した。
- ネイティブ有効画素225,065、再ラップ最大誤差0.0002225464 rad（基準0.001 rad未満）。
- 出力3400×3400、EPSG:4326、全5画像のグリッド一致、コヒーレンス範囲とラベル/欠損値対応を確認。
  アンラップのジオコード後有効画素は28,060。preview.pngも目視し、全欠損や明らかな上下左右の逆転は見られない。
- まとめ: real-acceptance.json。物理変位の精度や成分間の位相オフセットを確定したものではない。
- 合格したイメージIDにローカル別名sentinel-1-stack:runtime-validationを付与。devは維持し、公開はしていない。


### 標準9×3 looksの最終結果

- 全16工程が終了コード0で完了。SNAPHU計算時間は3分38.5秒。
- 工程1〜5完了後の工程6でSIGINT中断し、同じ設定・fingerprintで再開。完了済み工程は再実行されなかった。
- export --include-unwrappedも終了コード0で5種のGeoTIFFを生成。
- この監視プロセスもUID修正前に起動していたため、初回の最終検査は同じPermission denied。
  データ・検査基準を変えずホストUID/GIDで再検査し成功。初回失敗ログは保存している。
- 再ラップ最大誤差0.0000428562 rad（基準0.001 rad未満）、ネイティブ有効画素377,171。
- 出力3400×3400、EPSG:4326、5画像のグリッド一致、コヒーレンス範囲とラベル/欠損値対応を確認。
  アンラップのジオコード後有効画素80,856。
- まとめ: standard-real/acceptance.json。物理変位精度の保証ではない。

### 今回完了した範囲と残作業

通常のランチャー/ISCE2起動、NumPy1.26.4の維持、依存検査、独立ビルド2回と構成一致、
各XML100回・全114テスト10回、実データ2観測1ペアの3×1/9×3での中断再開・全工程・GeoTIFF検査まで完了。
旧devと元のAnakは維持し、試験は2つの独立したwork_dirで実施した。
アンラップ時間の約82分/約3分39秒は、この入力・設定・実行環境での観測値であり、一般的な所要時間を保証しない。

残る作業は、候補の正式版Dockerfile/Composeへの採用判断と整理、別Linux環境での確認、CI、ソース/公開版の整理。
旧devの異常終了の根本原因は未確定。公式Python3.11.10とCondaの数値ライブラリを併用する構成の検証結果である。
候補の成功を、旧devや未検証の別環境の合格と解釈しない。

## 通常構成への反映（2026-09-20）

- ルート Dockerfile に候補cのレシピを反映。Compose はこの Dockerfile を明示。
- lock を `environment/linux-64.explicit.txt`、起動スクリプトを `environment/runtime-entrypoint.sh` に配置。
  比較時のファイルと内容は同一。調査用レシピ・記録は保存。
- 旧 dev を `sentinel-1-stack:dev-before-runtime-20260920` に退避。
- 新 dev: `sha256:a09706d8633254a5ffb3c9a8820ffa2f0e3d0e32e2e2a490a3bea926b295bf53`。
- 通常ビルドの記録: `integration-build.log`。
- `integration-inventory.json` は `runtime-c-rebuild-2-inventory.json` とバイト単位で一致。
- `acceptance-integration/result.json`: pip check、XML100回、全114テスト10回がすべて成功。
- `integration-functional.log`: ホストUID/GIDでPython/子Python/ISCE起動、ネイティブ共存、合成SNAPHU、中断再開が成功。
- `integration-launcher.log`: 既定devのランチャーで、別work_dirの実データ設定をprepare --dry-run。生成や実処理は行わず成功。
- Compose の設定構文検査は成功。ただし直接起動はホストの `/` と `/mnt/ssd` が private mount のため
  `not a shared mount` で失敗（`integration-compose.log`、`integration-compose-work.log`）。
  ホストのmount属性は変更しない。通常のランチャーは成功している。READMEのWSLマウント手順を参照。
- 実データの全工程は先の同構成で検証済み。配置整理後の再ビルドでは再実行していない。
- 別Linux環境・CI・バージョン付き配布・正式公開は引き続き未完了。

## CI設定（2026-09-20）

- `.github/workflows/ci.yaml`: main push / pull_request / 手動実行。
  Ubuntu24.04、linux/amd64、ビルド40分・検査15分・ジョブ60分上限。
- 調査用検査を `tests/runtime/` に整理し、`scripts/check_ci.py` に共通実行手順を追加。
- ローカルでworkflowと同じビルドコマンドが成功（キャッシュ利用）。ciタグはdevと同じimage ID。
- ローカルでCI用スクリプト全体が成功。環境・Python起動・TIFF codec・合成SNAPHU・中断再開、
  pip check、XML100回、全114テスト10回を確認。
- actionlint1.7.12でworkflow構文検査成功。存在しないイメージでは失敗終了・JSON記録を確認。
- ローカルログ: `build/ci-local/`（Git管理外）。
- Git remote未設定のため、GitHub上の実行・artifact保存は未確認。設定実装とローカル検証のみ完了。
- ソースのcommit/push、レジストリ公開は行っていない。

## 2026-09-21 配布候補 rc1：最終検査不合格

ソースcommit `59f3531a30ab97bc828880707584d682f0bebb4c` から作成。
初回候補は検査成功したが、配布用メタデータを整えた最終候補は
XML100回・全体テスト9回成功後、suite-09で終了139。
`test_missing_orbit_does_not_download_any_eof` 実行中、argparseから呼ばれた
`shutil.get_terminal_size` 内でSegmentation fault。原因は未確定。
過去の合格結果をもって今回を合格にはしない。保存・load・initは成功。
最終ID: `sha256:e3ad061f5936cb9aa28a03889eb78d3fd92a457518717596ebb3e50648c5fe04`。配布・実データ検証用としての採用は保留。
全ログ・manifest・checksumは `dist/0.1.0-rc1/` に保存。
