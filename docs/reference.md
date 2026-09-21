# 詳細リファレンス

Linux を主対象とする Sentinel-1 IW SLC の取得・ISCE2 処理環境。
現在実装済みの自作コマンドは **`init`・`download`・`orbit`・`dem`・`prepare`・`run`・`export`**です。
位相・コヒーレンスの品質確認PNGとHTMLレポートも作成できます。
SLC 取得は明示的な操作です。他の処理から自動実行しません。

操作を始める方は [操作マニュアル](操作マニュアル.md) を開いてください。
作業ディレクトリの作成から順番に説明しています。以下は機能別の詳しい説明です。

## 環境

- 実行用 Python 3.11.10、NumPy 1.26.4、GDAL 3.10 系
- ISCE2 2.6.5（`b7702cfbd571681a133ebc5c3b23a10c78d52706`）
- `asf_search==14.0.0` の `ASFSession` で Earthdata の認証リダイレクトに対応
- Conda 依存は `environment/linux-64.explicit.txt`、追加 Python 依存は `environment/download-requirements.txt` に固定
- ベースイメージは Dockerfile の digest で固定（linux/amd64）

Wheel の取得は別のビルドステージで行い、ISCE2 環境へのインストールはオフラインで行います。
元イメージの pip がビルド中のネットワーク取得で異常終了したため、この構成にしています。
追加前後のバージョン比較と依存検査は `scripts/install_download_dependencies.py` で行います。

```bash
docker build --platform linux/amd64 -t sentinel-1-stack:dev .
```

ホストには Docker・Bash・Python 3.9 以上が必要です。Ubuntu 22.04 / WSL2 では
標準の `python3` を使えます。ホストへの PyYAML や ASF ライブラリの追加は不要です。

## 設定と初期化

最初に利用者自身が作業ディレクトリを決め、ASFから保存したJSON／GeoJSONを置きます。
次に `config/` を作り、設定例とASFファイルをその中にまとめます。
具体的なコマンドは [操作マニュアルの手順1〜5](操作マニュアル.md) を参照してください。

```text
<work_dir>/
└── config/
    ├── project.yaml
    └── asf-results.geojson
```

この配置では、設定の先頭を次のようにします。

```yaml
work_dir: ..
schema_version: 1
```

相対的な `work_dir` は **YAMLのあるディレクトリ** が基準です。
`config/project.yaml` の `..` は、その一つ上の作業ディレクトリを指します。
絶対パスでも指定できます。`paths` 以下は `work_dir` 内の相対パスです。
`paths.dem` はDEMファイル名で、取得後に設定します。

```bash
scripts/run.sh --image sentinel-1-stack:0.1.0-rc1 init /path/to/work/config/project.yaml
```

`/path/to/work/config/project.yaml` は実際の設定ファイルに置き換えます。
`init` は設定確認と `input/slc`・`input/orbit`・`input/dem`・`input/aux`・
`processing`・`output`・`logs`・`config` のディレクトリ作成だけを行います。
設定・ASFファイルのコピーや移動、データ取得は行いません。
既存データは上書き・削除せず、同じ設定で再実行できます。
作成先に同名の通常ファイルがあれば作成前にエラーになります。
権限・容量などのIOエラーでは、途中まで作成したディレクトリが残る場合があります。

`run.sh` はホストと同じUID/GIDでコンテナを起動します。
ホストの作業ディレクトリはコンテナ内の `/work` に接続されます。
設定・入力JSON・CLIの `--out` の相対パスは、コマンド実行時の場所が基準です。
YAML内の `work_dir` の基準とは区別してください。

ランチャーは `.env` を読みません。`WORK_ROOT` の設定は不要です。
`--image` を省略すると `sentinel-1-stack:dev` を使うため、
候補イメージでの実処理ではマニュアルのように明示します。
イメージのビルドは自動では行いません。

WSLで外部SSDを使う場合は、実際にマウントされていることを確認してください。
Compose直接起動で `not a shared mount` が出る環境の手順は、既存の環境設定を確認してから対処します。
通常の操作はマニュアルに記載した `scripts/run.sh` から行います。

## SLC のダウンロード

rc4以降は途中切断・接続失敗・タイムアウト・短い応答・HTTP 429/500/502/503/504を追加3回まで再試行します。
`--retries N`（0以上）で変更でき、一括実行では `--download-retries N` を使います。
DNSには従来の短い再試行もあります。認証エラー、不正な範囲応答、証明書エラー等は繰り返しません。
未完成の `.part` のサイズからHTTP Rangeを要求し、206のContent-Rangeが一致した場合だけ追記します。
200で全体が返る場合は先頭から取得します。完成時は全体のMD5を検証し、不一致なら回数制限内で先頭から取り直します。
元のJSON・正常なZIP・失敗時の部分ファイルは残します。
応答範囲の検査は [HTTPの範囲取得仕様](https://www.rfc-editor.org/rfc/rfc9110.html#name-range) に基づきます。


rc3以降は `download CONFIG --jobs 2` で2ファイルを並列取得できます。既定は1です。
一括実行では `batch.sh CONFIG --download-jobs 2` を使います。旧rc2イメージにはこの機能は含まれません。
ファイルごとに独立した認証セッションを使い、サイズ・MD5検証後に正式名にします。
再試行後も失敗したら新しい取得を止め、実行中の通信終了を待って記録を確定します。
実行記録には並列数と製品ごとの downloaded / skipped / recovered / failed / cancelled / pending 等を保存します。


最新版のホスト起動スクリプトでは `scripts/run.sh download CONFIG` とJSONを省略できます。
設定YAMLと同じ場所の `.json`／`.geojson` が1件なら自動選択し、0件・複数件では停止します。
明示指定は `scripts/run.sh download CONFIG --json JSON`。従来の位置引数 `download CONFIG JSON` も使えます。
この自動選択はホスト側の機能で、コンテナ内のCLIを直接使う場合はJSONの位置引数を指定します。


ASF Vertex から保存した GeoJSON `FeatureCollection` を指定します。
拡張子は `.json` / `.geojson`、各製品に `url`, `fileName`, `bytes`, `md5sum` が必要です。
現在の対象は Sentinel-1 **IW SLC の ZIP**です（GRD やバースト製品は対象外）。

計画の確認（ASF への通信・認証・作業ファイルへの書き込みなし）:

```bash
scripts/run.sh download config/agung.yaml /path/to/scenes.geojson --dry-run
```

JSON は作業ルートの外にあっても指定でき、読み取り専用でコンテナへ接続します。
**1ファイルを明示指定**し、周囲にある他の JSON は対象にしません。
入力 JSON は成功・失敗を問わず元の場所に残します。
保存先の既定は `<work_dir>/input/slc`、ログは `<work_dir>/logs/downloads` です。
`paths.slc` / `paths.logs` を設定した場合はその指定を使います。
製品一覧・合計容量を表示しますが、既存 ZIP の MD5 検証は通常実行時に行うため、
合計容量は追加ダウンロード量とは限りません。

### Earthdata 認証を接続して実行

ホストの `~/.netrc` を使います。認証情報をイメージ・YAML・Git に書き込みません。

```text
machine urs.earthdata.nasa.gov
  login YOUR_USERNAME
  password YOUR_PASSWORD
```

```bash
chmod 600 ~/.netrc
scripts/run.sh download config/agung.yaml /path/to/scenes.geojson
```

認証ファイルを変更する場合だけ `EARTHDATA_NETRC=/absolute/path/credentials` を指定します。
実際に取得するコマンドだけが認証ファイルを読み取り専用で接続します。
HTTP 401/403 は認証・アクセス権のエラーとして表示します。

保存先を変更する例:

```bash
scripts/run.sh download config/agung.yaml /path/to/scenes.geojson \
  --out /mnt/other-disk/slc
```

`--out`（`-out` も可）と `--log-dir` は **ホスト側のパス** で指定します。
JSON・設定ファイルの引数と同様、相対指定は実行時のカレントディレクトリが基準です。
作業ルート外の出力先も利用できます。存在しなければ取得時に作成し、dry-run では作成しません。
この場合、最も近い既存の親ディレクトリをコンテナへ接続します。
別プロジェクトは別の YAML で `work_dir` を設定し、その YAML で `init` してください。
未初期化の作業ルートでは取得前にエラーにします。

### 保存・失敗・再実行

- 取得開始前（認証後の転送先を含む）の DNS エラーは、1秒・2秒・4秒待って最大3回再試行し、端末とログに表示します。解消しなければ停止します。認証エラーは再試行しません。rc4以降の受信途中の切断には別途 `--retries` の回数制限付き再試行を適用します。
- `.part` に取得し、**サイズと MD5 が一致した後にだけ**正式な ZIP 名にします。
- 正常な既存 ZIP は検証してスキップします。完成済みの `.part` は正式名に変更します。
- 未完成の `.part` はrc4以降、HTTP Rangeで続きからの取得を試みます。サーバーが範囲取得に応じない場合は先頭から取得し直します。
- 不正な既存 ZIP がある場合は停止します。置換する場合は `--replace-invalid` を明示します。
  この場合も新しいファイルの検証成功まで既存 ZIP を残します。
- 入力 JSON は読み取り専用で扱います。移動・削除・アーカイブはしません。
- 途中の取得失敗・Ctrl+C では入力を残します。再実行は同じコマンドです。
- 保存先と入力 JSON をロックし、このツール同士の同時実行を防ぎます。
  **別ツール `download-sentinel-1` と同じ保存先へ同時実行しないでください。**
- 進捗とエラーは端末と `.log` に出力します。`.json` には製品ごとの状態、期待サイズ・MD5、
  入力 JSON の SHA256、時刻、作業ルート・保存先を記録します。認証情報や URL のクエリは記録しません。
- 終了コード: 成功 `0`、取得・IO・検証失敗 `1`、入力不備 `2`、中断 `130`。

入力検証やロック取得までのエラーは端末に表示します。ログファイルはロック取得後に作成します。

旧コマンド名 `download-slc` は `download` の別名として利用できます。
WSL の DNS 応答に問題が出る場合、必要に応じて
`RES_OPTIONS=edns0 scripts/run.sh download ...` のように設定するとコンテナにも引き継ぎます。

既存の `compose.yaml` / `compose.download.yaml` は手動接続用として残しています。
通常は `scripts/run.sh` を使ってください。Compose の作業ルート変数は
`SENTINEL_STACK_WORK_DIR` に変更し、古い `.env` の `WORK_ROOT` は参照しません。

Python を直接使う場合も `python -m sentinel_1_stack init CONFIG`、
`python -m sentinel_1_stack download CONFIG JSON` の形式です。この場合は同じ OS 上の
`work_dir` を使い、Docker マウントは行いません。コンテナ内だけで使う場合は、
設定内の `work_dir` もコンテナで見えるパスにしてください。

## 軌道ファイルの取得

SLC のダウンロードが完了したら、同じ設定 YAML を指定します。
`paths.slc`（既定 `input/slc`）直下の Sentinel-1 A/B/C/D の IW SLC ZIP / SAFE 名から
衛星と観測開始・終了時刻を読みます。JSON は不要です。ZIP 本体の再検証・展開はしません。
`.part` は無視して件数を表示し、対象 SLC が0件ならエラーにします。
取得途中に実行した場合、SLC 取得完了後にもう一度実行してください。

```bash
RES_OPTIONS=edns0 scripts/run.sh orbit config/agung.yaml --dry-run
RES_OPTIONS=edns0 scripts/run.sh orbit config/agung.yaml
```

`--dry-run` は **ASF に軌道の問い合わせを行います**。取得予定の EOF 名・種類を表示しますが、
EOF・ログ・作業ディレクトリを作成しません。SLC の `download --dry-run` とは通信の有無が異なります。

標準では **精密軌道 POEORB のみ**を使います。全 SLC の計画を確認してから EOF の取得を始めるため、
精密軌道がない観測があれば取得前に停止します。
速報軌道 RESORB を許可する場合だけ、次のように指定します。

```bash
RES_OPTIONS=edns0 scripts/run.sh orbit config/agung.yaml --allow-restituted --dry-run
RES_OPTIONS=edns0 scripts/run.sh orbit config/agung.yaml --allow-restituted
```

これは「POEORB があれば POEORB、なければ RESORB」という指定です。
2026-09-17 の確認では、Anak の 2026-09-03 の S1D 観測は RESORB が返されました。
精密軌道を使いたい場合は公開を待って再実行してください。

- 保存先は `paths.orbit`（既定 `<work_dir>/input/orbit`）、ログ・実行記録は `<paths.logs>/orbits`。
- `--out` と `--log-dir` は SLC 取得と同じくホスト側のパスで指定できます。
- 正常な既存 POEORB は、対応期間を確認して通信なしで再利用します。
  RESORB しかない場合は再問い合わせし、POEORB が提供されていれば追加取得します。既存 RESORB は保持します。
- 同じ EOF が複数 SLC を覆う場合、取得は1回だけです。
- XML の衛星名・種類・ファイル名・有効期間・座標系・時刻系と軌道ベクトルを検証します。
  ISCE2 の読み込みに合わせ、観測の前後60秒も覆うことを確認します。
- `.EOF.part` に取得し、検証後に `.EOF` に変更します。途中ファイルは次回取得時に先頭から書き直します。
- 壊れた既存 EOF は上書きせずエラーにします。該当ファイルを確認・退避してから再実行してください。
- ログには選択・再利用・エラーを表示し、JSON に SLC と EOF の対応、軌道種類、有効期間、取得先、SHA256 を記録します。
- DNS エラーは SLC と同じく最大3回再試行します。終了コードは成功0、取得・検証失敗1、入力不備2、中断130です。

参考の `fetchOrbit_asf.py` の観測期間照合を踏まえ、取得元には
[ASF 公式 s1-orbits](https://github.com/ASFHyP3/sentinel1-orbits-py) と同じ
`https://s1-orbits.asf.alaska.edu/scene/` と公開 S3 を使用しています。
Earthdata の認証は不要で、起動スクリプトも軌道取得時には `.netrc` を接続しません。
追加ライブラリはありません。

軌道取得の検証内容・制限は [environment/orbit-validation.md](../environment/orbit-validation.md) を参照してください。

## 画像範囲から DEM を取得・作成

取得済み SLC の画像範囲を自動で読み取ります。緯度・経度を手入力する必要はありません。

```bash
RES_OPTIONS=edns0 scripts/run.sh dem config/project.yaml --dry-run
RES_OPTIONS=edns0 scripts/run.sh dem config/project.yaml
```

`config/project.yaml` は使用する設定ファイルに置き換えてください。
`paths.slc` の ZIP / SAFE 内の `manifest.safe` を読み、全画像を覆う範囲を計算します。
ZIP 全体は展開しません。上下左右に既定0.1度の余白を加え、1度タイルの外側へ丸めます。
`--margin 0.2` のように余白を変更できます（0〜1度）。
`processing.bbox` や `swaths` による切り出しは行わず、SLC 全体を対象とします。
未完了の `.part` は対象外なので、SLC 取得完了後に実行してください。

処理の順番は次のとおりです。範囲・対象タイル・実際の `dem.py` コマンドを表示します。

1. 画像範囲と必要な SRTM 1秒角タイルを計算する。
2. [ESA STEP の SRTMGL1 配布一覧](https://step.esa.int/auxdata/dem/SRTMGL1/) を問い合わせる。
3. タイルを HTTPS で取得し、ZIP の CRC・HGT 名・サイズ・欠損画素を確認する。
4. ISCE2 の `dem.py -a stitch ... -s 1 -l -k -r -c` で結合し、EGM96 高から WGS84 楕円体高へ変換する。
5. ISCE2/GDAL で基準面・寸法・座標範囲・欠損値を確認し、完成した DEM 一式をまとめて保存する。

追加ライブラリ・Earthdata 認証は不要です。`dem.py` は取得済みタイルに対してローカル処理だけを行います。
`--dry-run` は画像範囲と配布一覧を確認し、DEM・キャッシュ・ログを作りません。
既存 DEM がある場合は内容の検証のみで、一覧への通信も省略します。

### 未配布タイル・欠損画素

未配布タイルや HGT 内の欠損画素がある場合、通常は停止します。
**0m（EGM96 高）で補完して進める場合だけ** `--fill-missing-zero` を付けます。
その後、補完した箇所も WGS84 楕円体高へ変換します。

```bash
RES_OPTIONS=edns0 scripts/run.sh dem config/project.yaml --fill-missing-zero --dry-run
RES_OPTIONS=edns0 scripts/run.sh dem config/project.yaml --fill-missing-zero
```

未配布を自動的に「海」と判断する処理ではありません。陸域の欠損も0mになるので、表示された範囲を確認してください。
HTTP エラー・DNS エラー・壊れた ZIP を0mで置き換えることはせず、取得エラーとして停止します。
DNS 失敗の再試行は SLC / 軌道と同じです。

Anak の6画像では、画像全体の範囲は約 `[-7.692004, -5.340134, 103.508125, 106.171051]`、
取得範囲は `[-8, -5, 103, 107]`（南・北・西・東）の12タイルでした。
2026-09-17 の配布一覧では `S08E103`・`S08E104`・`S07E103`・`S07E104` が未配布です。

### 出力と再実行

```text
input/dem/
├── tiles/                       # 検証済みの取得元 ZIP キャッシュ
└── srtm1_S08E103_S05E107/        # 取得範囲に応じた名前
    ├── dem.wgs84                # ISCE2 に渡す DEM 本体
    ├── dem.wgs84.xml
    ├── dem.wgs84.vrt
    └── dem.json                 # 元画像・範囲・取得元・補完内容・SHA256
```

実行ログ・記録は `<paths.logs>/dem` に保存します。
`--out` で DEM 一式とキャッシュの保存ディレクトリ、`--log-dir` でログ保存先を変更できます。
ホスト起動スクリプトでは、いずれもホスト側のパスで指定します。
正常な既存 DEM は再検証して再利用します。既存 DEM に0m補完がある場合、再利用にも `--fill-missing-zero` が必要です。
破損した既存 DEM・キャッシュは上書きせず停止します。
中断時は完成していない DEM を正式な出力にせず、検証済みタイルは再実行で再利用します。

設定 YAML は自動変更しません。完了時に表示される値を `paths.dem` に記入してください。

```yaml
paths:
  dem: input/dem/srtm1_S08E103_S05E107/dem.wgs84
```

`paths.dem` は後続処理への入力指定で、`dem` コマンドの保存先指定ではありません。
`--out` で作業ルート外に保存した場合は、後続処理に使う前に DEM 一式を `work_dir` 内に配置します。
現時点では日付変更線をまたぐ範囲、余白込みで南緯56度〜北緯60度を超える範囲、100タイルを超える範囲はエラーにします。
この DEM は SRTM の標高データで、SLC の撮影時点の地形を再構築するものではありません。

検証内容と既存環境での全体テストの不安定さは [environment/dem-validation.md](../environment/dem-validation.md) に記録しています。

## 検証

オフラインテスト（実データ取得・認証不要）:

```bash
docker run --rm \
  --mount type=bind,source="$PWD",target=/project,readonly \
  --workdir /project \
  sentinel-1-stack:dev python -m unittest discover -s tests -v
```

既存 Python パッケージの版が変わっていないことと、ASF/Shapely・GDAL・ISCE2・SNAPHU の
同時利用を確認:

```bash
docker run --rm \
  --mount type=bind,source="$PWD/tests",target=/tests,readonly \
  sentinel-1-stack:dev python /tests/check_environment.py
docker run --rm sentinel-1-stack:dev stackSentinel.py -h
```

現在の構成では NumPy 1.26.4 と互換な imagecodecs 2024.12.30 を固定し、
`python -m pip check` が成功することを確認しています。
旧構成で出ていた imagecodecs の NumPy>=2 診断は、安定化記録に残しています。
追加依存のインストールでは既存パッケージの版を維持し、Docker ビルドでも依存整合性を検査します。
診断と追加パッケージの一覧はイメージ内の
`/opt/conda/share/isce2/download-dependencies.json` に保存します。
この検証は SAR 実処理や全画像コーデックの検証を代替するものではありません。

参照: [ASF の認証・ダウンロード](https://docs.asf.alaska.edu/asf_search/downloading/)

## 処理スクリプトの生成

SLC・軌道・DEM の取得後、YAML の `paths.dem`、`processing.swaths`（例: `[3]`）、
`bbox`、`reference_date` を明示してください。現在は `workflow: interferogram` に対応します。

```bash
scripts/run.sh prepare config/project.yaml --dry-run
scripts/run.sh prepare config/project.yaml
```

`prepare` はネットワークを使わず、ローカル入力を確認して `stackSentinel.py` に引数を渡します。
ローカル軌道は POEORB を優先し、未取得の場合は取得済み RESORB を利用して種別を表示します。
軌道の新規ダウンロードはしません。`<work_dir>/<paths.processing>/`（既定: `processing/`）
の直下に `project.yaml`、`slc_inputs.txt`、選択した軌道のコピー、
`configs/`、`run_files/` を保存します。`prepare.log` と `prepare.json` は
`<work_dir>/<paths.logs>/`（既定: `logs/`）に保存します。既存ファイルがある場合はエラーで停止します。再生成する場合は先に内容を退避してください。
`--dry-run` は作業ルートを読み取り専用でマウントし、入力確認とコマンド表示だけを行います。

標準ではアンラップまで生成・実行します。`logs/prepare.json` には生成時点の
`run_files` と `skipped_run_files` を記録します。実行時は現在の YAML の unwrap 設定を読みます。
未フィルタ `fine.int`、フィルタ後 `filt_fine.int`、コヒーレンス `filt_fine.cor` は保持します。
wrapped phase/coherence の共通グリッドへのジオコードは `export` で実行します。

## 処理の実行

```bash
scripts/run.sh run config/project.yaml --dry-run
scripts/run.sh run config/project.yaml
# 中断・失敗後に、成功工程をスキップして再開
scripts/run.sh run config/project.yaml --resume
```

アンラップはメモリ消費が大きいため、既定で **1ペアずつ** 実行します。
他工程は生成済みスクリプトの並列数を維持します。メモリに余裕がある場合のみ、
`--unwrap-jobs 2` のように最大同時実行数を増やせます（生成済みの `wait` 境界は維持）。
これは実行時の上限なので、YAMLや生成ファイルを変更する必要はありません。
並列数を変えても完了済み工程を保持して再開できます。

```bash
# メモリ不足等で停止した後、アンラップを1ペアずつ再開
scripts/run.sh run config/project.yaml --resume --unwrap-jobs 1
```

終了コード `-9` / `137` は強制終了を示します。OOMとは限らないため、ホストの
`dmesg` と Docker/WSL のメモリを確認してください。1ペアでもメモリが不足する場合は、
利用可能メモリや処理範囲・分割処理を検討します。
手動変更した生成ファイルや、手動実行した結果を成功扱いで取り込む機能はありません。

通常はアンラップまで実行します。PGV などで省略する場合だけ、プロジェクト YAML の
`processing` 内に次の一行を追加します。この行を削除すれば標準動作に戻ります。

```yaml
  unwrap: false
```

unwrap の切替だけなら prepare の再生成は不要です。`--resume` で既に成功した工程は
スキップするため、省略したアンラップを後から追加実行できます。
他の処理設定や生成スクリプトが変わった場合は、そのまま再開せずエラーにします。
失敗・中断した工程は工程の先頭からやり直すため、必要に応じて途中成果物を確認してください。

ログと実行記録は全て `paths.logs` に置きます。全体は `run.log` / `run.json`、
各コマンドは `run_工程名_attempt試行番号_コマンド番号.log` です。
並列コマンドの終了コードをそれぞれ確認し、失敗時には同じ組の残りを停止して次へ進みません。
ISCE2 自身の `isce.log` も `logs/` に保存し、`processing/isce.log` はそこへのリンクです。
実行中の同一プロジェクトへの重複起動は拒否します。ネットワークへの接続はありません。

検証結果・共通バーストの確認・環境の制約は `environment/prepare-validation.md` を参照してください。

## 位置合わせの設定とNESDエラー

`config/example.yaml` に、このツールで対応する全処理パラメータを記載しています。
ISCE2 の全内部設定や、未対応のGPU・電離層補正オプションまで公開するものではありません。
未知の processing / pairs / execution キーはエラーにします。

```yaml
processing:
  esd_coherence_threshold: 0.85
  snr_misreg_threshold: 10.0
  num_overlap_connections: 3
  virtual_merge: true
  remove_filter_effect: false
```

それぞれ stackSentinel.py の `--esd_coherence_threshold`、`--snr_misreg_threshold`、
`--num_overlap_connections`、`--virtual_merge`、`-rmFilter` に対応します。
NESD の接続数は最終干渉ペアの `pairs.connections` とは別です。
省略時は上記の既定値を使うため、既存 YAML の挙動は変わりません。

`No points left for reliable ESD estimate` の場合は、日本語で原因・対処手順を表示し、
run.log / run.json にも残します。コヒーレンス分布と有効画素数を確認してから、閾値、
安定した陸域を含む範囲、NESD 接続数を検討してください。閾値を下げるだけでは精度は保証されません。
設定を変えた場合は processing/ と logs/ を退避し、設定ファイルを残して init → prepare → run
をやり直します。途中成果物は自動削除せず、設定変更後の単純な --resume も認めません。


## GeoTIFF出力と品質確認

```bash
scripts/run.sh export config/project.yaml --dry-run
scripts/run.sh export config/project.yaml
```

filter_coherence まで完了した処理に対して実行します。出力は `output/geocoded/`。
各ペアの `phase_final.tif`（フィルタ後）、`phase_raw.tif`（未フィルタ）、`coherence.tif` は
同じEPSG:4326グリッド、NaNのNoDataです。位相単位はラジアン、ISCE2の符号を保持します。
YAMLの `export.pixel_size_degrees`（既定0.0001度）と `export.resampling`（bilinear / near）で指定します。
処理bboxの四辺に一致するよう画素数を丸めるため、厳密な画素間隔は quality.json に記録します。
位相は単位複素数の実部・虚部を補間した後 atan2 で復元し、±π境界で角度を直接平均しません。
振幅で重み付けはしません。欠損画素と位相ベクトルが相殺した画素はNoDataです。
低コヒーレンス画素は自動削除しません。後段で適切な品質マスクを指定してください。

`report.html` と各ペアの `preview.png` で未フィルタ位相・フィルタ後位相・コヒーレンスを比較できます。
`quality.json` にNESD推定値・有効画素数・海域込みのコヒーレンス統計を記録します。
ログ・実行記録は `logs/export.log` / `logs/export.json`。既存成果物は上書きしません。
標準出力はラップ位相用です。アンラップ結果の追加出力は次節を参照してください。PGVアルゴリズム自体は含みません。

正式版までの残作業は [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md) を参照してください。

### アンラップ位相と連結成分のGeoTIFF

SNAPHUのアンラップまでツール上で正常終了した後、明示的に追加出力できます。

```bash
scripts/run.sh export config/project.yaml --include-unwrapped --dry-run
scripts/run.sh export config/project.yaml --include-unwrapped
```

通常の3枚に `phase_unwrapped.tif`（位相、rad、NaN欠損）と
`connected_components.tif`（Byteの成分番号、0は無効）を加えます。
入力はISCE2の `filt_fine.unw`（Float32の振幅・位相2バンド）と
`filt_fine.unw.conncomp`（Byte）、それぞれのVRTです。
完了記録、寸法、型、ファイルサイズを検査し、不完全なデータは公開しません。
全ペア成功後にまとめて公開するため、途中失敗では既存成果物は変わりません。
既存の `output/geocoded` は上書きしません。再出力する場合は先に退避してください。
`--dry-run` は実行記録・設定の確認までで、ラスタの完全性検査は実出力時に行います。

アンラップ位相と成分番号は、成分の境界を混ぜないよう**最近傍補間固定**です。
wrapped位相/coherenceの `export.resampling` とは別です。成分0および非有限位相は除外し、
有効成分内の位相0は保持します。成分番号はペアごとの番号であり、別ペア間で対応を意味しません。
成分ごとの位相オフセットは未確定です。基準点を決めたLOS変位への換算は行いません。
連結成分を出さないアンラップ方式には、この追加出力は対応しません。

### 環境の安定性を繰り返し検証する

```bash
python3 scripts/validate_stability.py \
  --image sentinel-1-stack:dev \
  --output /tmp/sentinel-stability-check
```

対象イメージのIDを固定して、依存関係検査、ASF読み込み後のXML再現試験100回、
全体テスト10回を実行します。毎回新しいコンテナを使い、外部通信・実データ更新はしません。
最初の失敗やタイムアウトで停止し、各ログと `result.json` を残します。
出力先には未作成のディレクトリを指定してください。
この検査の合格だけで、実データのアンラップ・再開・再ビルドの検証が完了するわけではありません。
現在の候補比較と制限は [安定化調査](../environment/stabilization/REPORT.md) を参照してください。

通常の Dockerfile と Compose は、この検証済み構成を使用します。
Conda の環境設定（GDAL/PROJ のデータパスなど）を読み込んだ後、
`environment/runtime-entrypoint.sh` で公式 Python 3.11.10 を選択します。
ISCE2 の子プロセスにも同じ Python を使います。Conda 側の Python はビルド・依存用です。
`environment/environment.yml` は依存の概要で、ビルド時の入力は explicit lock です。
依存更新時は lock と Dockerfile を更新し、同じ検証を再実行してください。

2026-09-20 の検証構成は独立2ビルドの反復試験と、Anak の2観測・1ペア
（3×1 / 9×3 looks）の中断再開・アンラップ・5種 GeoTIFF 検査を完了しています。
比較用タグ `sentinel-1-stack:runtime-validation` と当時のレシピ・記録は残しています。
これはローカル開発構成への採用であり、別 Linux 環境での実処理検証・GitHub 上の CI 初回実行・正式公開は未完了です。

今回の切り替え前のイメージは `sentinel-1-stack:dev-before-runtime-20260920` に退避しています。
旧構成との比較には次のように明示して起動できます。

```bash
scripts/run.sh --image sentinel-1-stack:dev-before-runtime-20260920 prepare config/project.yaml --dry-run
```

## 自動ビルド・テスト（CI）

`.github/workflows/ci.yaml` は、main への push、プルリクエスト、Actions 画面の手動実行で動きます。
Ubuntu 24.04 の GitHub runner 上で linux/amd64 イメージをビルドし、次を確認します。

- 依存整合性と NumPy 1.26.4 の維持。
- 実行用 Python・ISCE2・GDAL・ASF の共存と TIFF 圧縮の往復検査。
- 合成位相による SNAPHU アンラップと、模擬処理の中断・再開。
- XML 再現試験100回、全単体テスト10回（初回失敗で停止、成功するまでの再試行はしません）。

ビルド失敗もジョブの失敗として扱います。ビルド・検査ログとパッケージ一覧は
Actions の Artifacts に14日間保存します。テスト用コンテナはネットワークを無効にし、
SAR データや Earthdata 認証情報を使いません。イメージのレジストリ公開は行いません。
ビルド自体は依存パッケージの取得にネットワークを使います。

同じ検査をローカルで実行できます。出力先には未作成のディレクトリを指定してください。

```bash
docker build --platform linux/amd64 --build-arg BUILD_JOBS=2 -t sentinel-1-stack:ci .
python3 scripts/check_ci.py --image sentinel-1-stack:ci --output build/ci/checks
```

失敗した場合は `result.json` と該当検査のログ、反復試験では `stability/result.json` を確認します。
実データの全工程・画質評価・独立2ビルドの比較はリリース前の別検証です。
GitHub 上の実行結果は、リポジトリの Actions 画面で確認してください。ローカルでの検査結果とは別に扱います。

Workflow の構文は [GitHub Actions 公式資料](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) を参照してください。

## 配布候補

実データ検証用の `sentinel-1-stack:0.1.0-rc1` の使用・ファイル配布手順は
[配布候補の手順](releases/0.1.0-rc1.md) を参照してください。正式公開前の候補です。

## 補助的な一括実行

入力取得から処理・出力をまとめて実行する場合は [一括実行](一括実行.md) を参照してください。通常は各工程を個別に確認する操作マニュアルを使います。
