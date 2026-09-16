# Sentinel-1 Stack 開発・再現環境 TODO

## 目的

Sentinel-1 の InSAR / SBAS 用スタック生成環境を、自分用のツールとして整理する。

目標は次の3点。

- 一度構築した環境を、別の Linux PC でもすぐ使える（主な利用環境は Linux）
- 1年後でも、できるだけ同じ環境・同じ処理条件で再実行できる
- 将来、SAR エンジニアとして開発する他のツールも整理しやすい構成にする

Windows / WSL2 からの利用も想定するが、実装・検証は Linux を優先する。
Windows 対応の完了状況は別途記録し、Linux 版の完成条件には含めない。

## 今日の進め方（2026-09-16）

- 実装・コマンド実行は本人が行い、アシスタントは作業手順の提示・レビュー・問題の切り分けを担当する。
- アシスタントによる変更は、依頼された TODO・方針の文書更新にとどめる。
- `master/sbas_test` に既存環境はあるが、流用せず、環境・処理コードとも完全に一から構築する。
- 検証用データの取得には、`~/sar-tools/` 配下に別ツールを新規作成する。JSON ファイルを引数に受け取り、ASF からダウンロードする。
- ダウンロードツールの仮称は `asf-download`。入力 JSON が ASF の検索結果か独自の検索条件かは、実装前に決める。
- Linux 上で、環境構築 → データ mount → 小規模な実処理 → 出力確認の順に動作を確認し、配布・保存を仕上げる。
- 今日完成を目指す対象は Linux 版。実処理で確認する範囲・期待出力を着手時に決め、未検証の項目は完了扱いにしない。

---

# 1. 基本方針

## 親ディレクトリ

SAR 関連の自作ツールは、以下にまとめる。

```text
~/sar-tools/
```

例:

```text
~/sar-tools/
├── sentinel-1-stack/
├── asf-download/                 # 仮称：JSON を入力に ASF からデータ取得
├── phase-gradient-voting/
├── phase-gradient-consensus/
├── sar-utils/
└── ...
```

---

# 2. 今回のプロジェクト名

以下で統一する。

```text
sentinel-1-stack
```

用途ごとの名前:

| 用途 | 名前 |
|---|---|
| ローカルディレクトリ | `~/sar-tools/sentinel-1-stack` |
| GitHub repository | `sentinel-1-stack` |
| Docker image | `sentinel-1-stack` |
| GHCR package | `ghcr.io/<github-user>/sentinel-1-stack` |
| 最初の正式版 | `1.0.0` |

例:

```text
sentinel-1-stack:1.0.0
```

または GHCR では、

```text
ghcr.io/<github-user>/sentinel-1-stack:1.0.0
```

---

# 3. コードとSARデータを分離する

Git で管理するコードと、巨大な SAR データは分離する。

## コード

```text
~/sar-tools/sentinel-1-stack/
```

## データ

データの保存場所はプロジェクト内の `data/` に固定せず、実行時にデータルートを指定できるようにする。
通常はコードと別の場所に置き、外部ディスク等の `/mnt/` 配下も同じ方法で扱えるようにする。

例（いずれかをデータルートとして指定）:

```text
~/sar-data/
└── sentinel-1/
    └── kilauea/

/mnt/sar-data/
└── sentinel-1/
    └── kilauea/
```

必要に応じて `~/sar-tools/sentinel-1-stack/data/` も指定可能にする。
研究データや生成物は GitHub repository と Docker build context に入れない。
プロジェクト内の `data/` は `.gitignore` と `.dockerignore` の両方で除外する。

## ASF ダウンロードツールとの接続

データ取得用の `asf-download` と、処理用の `sentinel-1-stack` は別プロジェクトとして管理する。
ダウンロード先は引数で指定できるようにし、`sentinel-1-stack` のデータルート内の `input/slc/` に保存する。

```text
入力 JSON
    ↓
asf-download
    ↓
<データルート>/input/slc/
    ↓
sentinel-1-stack（コンテナ内では /data/input/slc/）
```

最初の対象はスタック検証用の Sentinel-1 SLC とする。
処理側が必要とする ZIP / SAFE の扱いを決め、取得ツールと受け渡し形式をそろえる。
orbit・DEM・その他の補助データの準備方法は、SLC 取得とは別に決める。

---

# 4. 推奨プロジェクト構成

```text
~/sar-tools/sentinel-1-stack/
│
├── Dockerfile
├── compose.yaml
├── README.md
├── .gitignore
├── .dockerignore
│
├── src/
│   └── sentinel_1_stack/
│       ├── __init__.py
│       ├── cli.py
│       ├── pipeline.py
│       └── pairs.py
│
├── config/
│   ├── default.yaml
│   └── example.yaml
│
├── environment/
│   ├── environment.yml
│   ├── conda-lock.yml
│   └── versions.yaml
│
├── docker/
│   └── entrypoint.sh
│
├── scripts/
│   ├── run.sh
│   └── run.ps1              # Windows 対応時に追加
│
└── tests/
    ├── test_data/
    └── expected/
```

---

# 5. SAR データ側の構成

例:

```text
~/sar-data/sentinel-1/kilauea/
│
├── input/
│   ├── slc/
│   ├── orbit/
│   ├── dem/
│   └── aux/
│
├── config/
│   └── processing.yaml
│
├── work/
│   ├── reference/
│   ├── secondary/
│   ├── coreg/
│   └── interferograms/
│
├── output/
│   ├── wrapped/
│   ├── coherence/
│   ├── unwrapped/
│   └── timeseries/
│
└── logs/
```

Docker container からは、指定したデータルートを `/data` として扱う。
上記の構成は `/mnt/sar-data/sentinel-1/kilauea/` やプロジェクト内の `data/` に置いた場合も共通とする。

```text
host
~/sar-data/sentinel-1/kilauea
              │
              │ bind mount
              ▼
container
/data
```

これにより、ホスト側の保存場所が変わっても、container 内では常に次のように扱える。

```text
/data/input
/data/work
/data/output
```

データルートの指定・検証方針:

- `scripts/run.sh` の引数でホスト側のデータルートを指定する。
- `compose.yaml` を使う場合も、データルートを設定可能にし、特定のホストパスを埋め込まない。
- 相対パスは実行時のカレントディレクトリを基準に解決する。空白を含むパスも扱えるようにする。
- 処理設定では `/data` を基準にし、ホスト固有の `/mnt/...` 等を埋め込まない。
- 存在しないデータルートや必要な入力がない場合は、処理開始前にエラーにする。
- Linux では入力の読み取り権限と `work/`・`output/`・`logs/` への書き込み権限を確認する。
- ホスト側の利用者が生成物を操作できるよう、コンテナ実行時の UID / GID の扱いを決める。

---

# 6. ISCE2 の扱い

ISCE2 本体は Conda package に完全依存せず、source code を clone して使う。

方針:

1. GitHub から ISCE2 を clone
2. 使用する commit hash を固定
3. Docker build 時にその commit を checkout
4. 依存ライブラリも可能な範囲で固定

例:

```bash
git clone https://github.com/isce-framework/isce2.git
cd isce2
git checkout <commit-hash>
```

`versions.yaml` などに commit を残す。

例:

```yaml
pipeline_version: 1.0.0

isce2:
  repository: https://github.com/isce-framework/isce2.git
  commit: "<commit-hash>"

python: "3.10"
gdal: "<version>"
numpy: "<version>"
snaphu: "<version>"
```

---

# 7. Conda / Micromamba の役割

Conda / Micromamba は、主に Docker image 内部の依存関係管理に使う。

例:

```text
Docker image
├── Ubuntu
├── Micromamba
│   ├── Python
│   ├── NumPy
│   ├── SciPy
│   ├── GDAL
│   └── その他依存関係
│
├── ISCE2 fixed commit
├── SNAPHU
└── sentinel-1-stack
```

利用者には Conda を直接触らせない。

理想的には、

```text
conda activate ...
```

をユーザーが実行する必要がない構成にする。

---

# 8. Docker image を作る

Dockerfile を作成後、

```bash
cd ~/sar-tools/sentinel-1-stack

docker build   -t sentinel-1-stack:1.0.0   .
```

確認:

```bash
docker images
```

---

# 9. Docker image の使い方

Docker image は「完成済みの実行環境」。

```text
Dockerfile
    │
    │ docker build
    ▼
Docker image
    │
    │ docker run
    ▼
Container
```

SAR データは image に入れず、外部ディレクトリを bind mount する。

例:

```bash
docker run --rm   -v ~/sar-data/sentinel-1/kilauea:/data   sentinel-1-stack:1.0.0
```

外部 `/mnt/` 配下を使う場合の例:

```bash
docker run --rm \
  --mount type=bind,source=/mnt/sar-data/sentinel-1/kilauea,target=/data \
  sentinel-1-stack:1.0.0
```

---

# 10. Linux を主対象に、簡単に実行できるようにする

まず Linux 側で、以下のようにデータルートを指定して実行できる状態を目指す。

```bash
./scripts/run.sh /mnt/sar-data/sentinel-1/kilauea
```

`~/sar-data/...` やプロジェクト内の `./data` も、同じ引数で指定できるようにする。
内部では Docker を起動し、指定したディレクトリを `/data` に bind mount する。

## Windows 対応（追加対応）

Linux 版の動作確認後、Windows / WSL2 側でも以下のように実行できる状態を目指す。

```powershell
.\scripts\run.ps1 D:\SAR\kilauea
```

内部では `docker run` を実行する。

イメージ:

```text
Windows
   │
   └── run.ps1
          │
          ▼
       Docker
          │
          ▼
   sentinel-1-stack:1.0.0
          │
          ▼
        ISCE2
          │
          ▼
      interferograms
```

---

# 11. GitHub には何を保存するか

GitHub repository には以下を保存する。

```text
Dockerfile
source code
config templates
tests
README
environment files
version information
```

Docker image 本体は通常の Git repository には保存しない。

---

# 12. Docker image は GHCR に保存する

GitHub Container Registry (GHCR) を使う。

例:

```text
ghcr.io/<github-user>/sentinel-1-stack:1.0.0
```

ローカル image に tag を付ける。

```bash
docker tag   sentinel-1-stack:1.0.0   ghcr.io/<github-user>/sentinel-1-stack:1.0.0
```

push:

```bash
docker push   ghcr.io/<github-user>/sentinel-1-stack:1.0.0
```

別 PC では、

```bash
docker pull   ghcr.io/<github-user>/sentinel-1-stack:1.0.0
```

---

# 13. Docker image の NAS バックアップ

GHCR だけに依存せず、正式版 image は NAS にも保存する。

保存:

```bash
docker save sentinel-1-stack:1.0.0   | zstd   > sentinel-1-stack_1.0.0_linux-amd64.tar.zst
```

checksum:

```bash
sha256sum   sentinel-1-stack_1.0.0_linux-amd64.tar.zst   > SHA256SUMS
```

NAS 例:

```text
NAS/
└── software/
    └── docker/
        └── sentinel-1-stack/
            ├── 1.0.0/
            │   ├── sentinel-1-stack_1.0.0_linux-amd64.tar.zst
            │   ├── SHA256SUMS
            │   └── versions.yaml
            │
            └── 1.1.0/
```

復元:

```bash
zstd -d -c sentinel-1-stack_1.0.0_linux-amd64.tar.zst   | docker load
```

---

# 14. 再現性のために残すもの

正式な実験・論文では、最低限以下を記録する。

```text
Docker image tag
Docker image digest
sentinel-1-stack version
ISCE2 commit hash
processing config
input data information
input data checksum
```

例:

```yaml
software:
  image: ghcr.io/<github-user>/sentinel-1-stack:1.0.0
  digest: sha256:<digest>

pipeline:
  version: 1.0.0

isce2:
  commit: <commit-hash>
```

---

# 15. テストデータを用意する

小規模なテスト用 Sentinel-1 データと、期待する結果を保存する。

目的:

- 別 PC でも正常に動くか確認
- 1年後でも同じ pipeline が動くか確認
- image の破損や依存関係の問題を確認

理想:

```bash
docker run   sentinel-1-stack:1.0.0   test
```

結果:

```text
TEST PASSED
```

---

# 16. version 管理

正式版は Semantic Versioning を基本にする。

```text
1.0.0
1.0.1
1.1.0
2.0.0
```

研究・論文で使用した image では、`latest` に依存しない。

推奨:

```text
sentinel-1-stack:1.0.0
```

避ける:

```text
sentinel-1-stack:latest
```

---

# 17. 完成形

最終的な構成:

```text
                      GitHub
                         │
             ┌───────────┴───────────┐
             │                       │
             ▼                       ▼
      Git Repository                GHCR
                                    │
Dockerfile                    sentinel-1-stack
source code                       :1.0.0
config                             :1.1.0
tests                              :2.0.0
versions                              │
                                      │
                                docker pull
                                      │
                                      ▼
                                Linux（主対象）
                                Windows / WSL2（追加対応）

                     ＋

                    NAS
                     │
                     └── Docker image archive
                         *.tar.zst
```

---

# TODO

## Phase 0 — 新規構築方針と検証データの取得

- [x] 既存の `master/sbas_test` を流用せず、一から構築する方針を決める
- [ ] `~/sar-tools/asf-download/`（仮称）を独立したプロジェクトとして作成する
- [ ] 入力 JSON の種類・必須項目・検証方法を決める
- [ ] ダウンロード先を引数で指定可能にし、外部 `/mnt/...` 配下にも対応する
- [ ] ASF へのアクセスに `asf_search` を利用する方針を検討し、依存バージョンを記録する
- [ ] Earthdata 認証を設定し、認証情報は入力 JSON と分離する
- [ ] JSON 読み込みと対象一覧の表示を実装し、ダウンロード前に確認できるようにする
- [ ] 小規模なダウンロードを実行し、ファイルの完全性を確認する
- [ ] 再実行時に取得済みの正常なファイルを判別し、未完了ファイルを成功扱いにしない
- [ ] 入力 JSON と取得した製品の情報を記録する
- [ ] スタック処理に使うデータの範囲・期待出力と、orbit・DEM 等の準備方法を決める

参考: [ASF asf_search の概要](https://docs.asf.alaska.edu/asf_search/basics/)、[ダウンロード・認証](https://docs.asf.alaska.edu/asf_search/downloading/)

## Phase 1 — プロジェクト作成

- [x] `~/sar-tools/` を作成する
- [x] `~/sar-tools/sentinel-1-stack/` を作成する
- [ ] Git repository を初期化する
- [ ] GitHub に `sentinel-1-stack` repository を作成する
- [ ] 基本ディレクトリ構造を作成する
- [ ] `.gitignore` を作成する
- [ ] `.dockerignore` を作成し、データと生成物を build context から除外する
- [ ] README を作成する

## Phase 2 — ISCE2 環境

- [ ] 使用する ISCE2 commit を決める
- [ ] Python version を決める
- [ ] GDAL / NumPy / SciPy / SNAPHU 等の version を決める
- [ ] `versions.yaml` に記録する
- [ ] Micromamba / Conda environment を定義する
- [ ] ISCE2 を source から build できるようにする

## Phase 3 — Docker

- [ ] Dockerfile を作成する
- [ ] `sentinel-1-stack:dev` を build する
- [ ] container 内で ISCE2 が起動することを確認する
- [ ] Sentinel-1 stack processor が動くことを確認する
- [ ] ホストディレクトリを `/data` に mount できるようにする
- [ ] プロジェクト内の `data/` と外部 `/mnt/...` のどちらも指定できることを確認する
- [ ] Linux の UID / GID と読み書き権限を確認する

## Phase 4 — 自作ツール化

- [ ] `config.yaml` の仕様を決める
- [ ] pair network 生成を自動化する
- [ ] ISCE2 実行 wrapper を作成する
- [ ] interferogram 出力形式を整理する
- [ ] coherence 出力を整理する
- [ ] unwrap 処理を整理する
- [ ] CLI を作る

例:

```bash
sentinel-1-stack process /data/config/processing.yaml
```

## Phase 5 — Linux での実行手順

- [ ] `scripts/run.sh` を作る
- [ ] 引数でデータルートを指定し、コンテナの `/data` に mount する
- [ ] 存在しないパス・入力不足・権限不足を検出し、原因を表示する
- [ ] Linux 向けの準備・実行・結果確認の手順を README に書く

目標:

```bash
./scripts/run.sh /mnt/sar-data/sentinel-1/kilauea
```

だけで処理開始。

## Phase 6 — テスト

- [ ] 小さな Sentinel-1 test dataset を用意する
- [ ] expected output を作成する
- [ ] 自動テストを作る
- [ ] 新しい container でも同じ処理が動くことを確認する

## Phase 7 — v1.0.0

- [ ] Docker image `sentinel-1-stack:1.0.0` を作る
- [ ] image digest を記録する
- [ ] Git tag `v1.0.0` を作る
- [ ] GitHub release を作る
- [ ] GHCR に image を push する
- [ ] NAS に `.tar.zst` を保存する
- [ ] SHA256 checksum を保存する

## Phase 8 — Linux 版の最終確認

- [ ] 別の Linux PC またはクリーンな Linux 検証環境を用意し、検証条件を記録する
- [ ] GHCR から image を pull する
- [ ] SAR データを mount する（外部 `/mnt/...` 配下も含めて確認する）
- [ ] `scripts/run.sh` で実行する
- [ ] test dataset で正常動作を確認する
- [ ] 同じ主要出力が得られることを確認する

この確認まで成功したら、

```text
sentinel-1-stack v1.0.0
```

の Linux 版を完成とする。Windows は以下の追加対応として扱う。

## 追加対応 — Windows / WSL2

- [ ] `scripts/run.ps1` を作る
- [ ] Windows パスから Docker volume mount できることを確認する
- [ ] WSL2 / Docker Desktop 環境でテストする
- [ ] 別の Windows PC で image を pull し、同じテストデータで主要出力を確認する
- [ ] Windows 向けの準備・実行手順と検証状況を README に書く

---

# 最終目標

新しい Linux PC でも、

```text
Docker を準備
        ↓
docker pull
        ↓
scripts/run.sh <データルート>
        ↓
Sentinel-1 stack processing
```

だけで利用できる状態にする。

Windows / WSL2 でも、追加対応後は `scripts/run.ps1` から同じ image を利用できる状態を目指す。

利用者は以下を意識しなくてよい状態を目指す。

```text
ISCE2 build
Conda environment
Python dependencies
GDAL dependencies
PYTHONPATH
ISCE_HOME
SNAPHU installation
```

**Docker image を `sentinel-1-stack` の実行環境そのものとして扱い、GitHub + GHCR + NAS の3つでコード・配布・長期保存を分担する。**
