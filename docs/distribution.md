# Dockerイメージのダウンロードと導入

対象はLinux x86_64（linux/amd64）。WindowsではWSL2＋Docker Desktopを使います。
Docker、Bash、curl、Python 3.9以上を事前に用意し、Dockerを起動してください。
以下の操作はLinuxまたはWSLのターミナルで行います。

## 1. 配布ファイルを取得する

ターミナルから3ファイルをダウンロードします。GitHubアカウントやGitのSSH設定は不要です。

まず保存先を決めます。**下記の `$HOME/sentinel-1-stack` は例です。フォルダ名・配置場所は自由に変更してください。**
`$HOME` はLinux／WSLのホームディレクトリを表します。
例えば `$HOME/sar-tools/s1-stack` に変更できます。

ここは配布ファイルと操作用ツールを置く場所です。
**SLCや解析結果を保存する作業ディレクトリは、導入後に操作マニュアルで別途決めます。**

```bash
# 保存先の例：必要に応じて、この行を変更してください
INSTALL_DIR="$HOME/sentinel-1-stack"

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
```

`mkdir -p` は保存先フォルダを作成し、`cd` はそのフォルダへ移動します。
続いて、同じターミナルでダウンロードします（イメージは約1GB）。

```bash
BASE_URL="https://github.com/geranium-0019/sentinel-1-stack/releases/download/v0.1.0-rc4"

curl -fLO "$BASE_URL/sentinel-1-stack-0.1.0-rc4-linux-amd64.tar.gz"
curl -fLO "$BASE_URL/sentinel-1-stack-0.1.0-rc4-source.tar.gz"
curl -fLO "$BASE_URL/SHA256SUMS"
```

取得するファイルは以下の3つです。エラーが出た場合は、取得を完了してから次へ進んでください。

- `sentinel-1-stack-0.1.0-rc4-linux-amd64.tar.gz`：Dockerイメージ
- `sentinel-1-stack-0.1.0-rc4-source.tar.gz`：起動スクリプト・設定例・マニュアル
- `SHA256SUMS`：ファイル破損を確認するためのチェックサム

ブラウザを使う場合は、[rc4の配布ページ](https://github.com/geranium-0019/sentinel-1-stack/releases/tag/v0.1.0-rc4)
のAssetsから同じ3ファイルを同じフォルダへ保存しても構いません。
GitHubが自動表示する「Source code」ではなく、上記の名前のファイルを選びます。

イメージには解析ソフトウェアが含まれます。SLC・DEMなどの観測データや認証情報は含みません。
圧縮ファイルの保存に加えて、Docker側にも展開後のイメージ用容量が必要です。

## 2. 確認して読み込む

ダウンロード先のフォルダで実行します。

```bash
sha256sum --ignore-missing -c SHA256SUMS
```

**イメージと操作用ファイルの両方について `OK` が表示されたことを確認してから、次のコマンドへ進めてください。**
`--ignore-missing` は、任意の検査ログなどをダウンロードしていない場合に使います。

```bash
docker load -i sentinel-1-stack-0.1.0-rc4-linux-amd64.tar.gz
tar -xzf sentinel-1-stack-0.1.0-rc4-source.tar.gz
cd sentinel-1-stack-0.1.0-rc4
```

読み込みと展開が終わったら確認します。

```bash
docker image inspect sentinel-1-stack:0.1.0-rc4 --format '{{.Id}}'
docker run --rm sentinel-1-stack:0.1.0-rc4 python --version
ls scripts/run.sh config/example.yaml

TOOL_DIR="$(pwd -P)"
IMAGE="sentinel-1-stack:0.1.0-rc4"
```

イメージID・Pythonのバージョン・2つのファイルが表示されれば導入完了です。
イメージを読み込んでも、ホスト側に操作用ファイルは作られないため、ソースの展開も必要です。

## 3. 自分のデータで処理する

`TOOL_DIR` は展開先、`IMAGE` は読み込んだイメージ名です。ここまでで両方設定できています。
**次は [操作マニュアル](操作マニュアル.md) の手順1へ進んでください。** 同じターミナルを使い、作業ディレクトリの作成から始めます。
新しいターミナルで再開する方法は操作マニュアルの末尾にあります。

ASFの検索結果JSONとEarthdata認証は、ご自身で用意してください。
作業ディレクトリには外部ディスクの `/mnt/…` も指定できます。
イメージと操作用ファイルは同じバージョンのものを使ってください。並列取得はrc3以降、自動再試行・途中再開はrc4以降に対応しています。

## 検証範囲

rc4は検証用の候補です。GitHub上でビルドした同じイメージに対して、依存関係、
ISCE2・GDAL等の読み込み、合成データのアンラップ、中断・再開、XML反復100回、
全単体テスト10回を確認し、成功した場合だけ配布ファイルを作成します。
`validation.tar.gz` と `image-inspect.json` で結果・イメージID・ソースcommitを確認できます。

過去のrc1では現在のWSL環境で反復検査中の異常終了があり、原因は未確定です。
GitHub上での成功は、その原因の解消や全PC・全観測データでの動作を保証するものではありません。
rc4そのものでの実データの通し検証は今後行います。

## 開発者向け：次の配布を作る

ソースとドキュメントのバージョンを更新してコミットした後、新しい `vX.Y.Z` または
`vX.Y.Z-rcN` タグをpushすると、配布workflowがビルド・検査・パッケージ作成・Releasesへの登録を行います。
失敗した場合は公開せず、Actionsに検査ログを残します。既存タグ・配布ファイルは上書きしません。
`-rcN` はGitHub上でもPrereleaseとして公開します。Dockerレジストリへのpushは行いません。

解析エンジン：[ISCE2公式リポジトリ](https://github.com/isce-framework/isce2)。
ISCE2のライセンス文書はイメージ内の `/opt/conda/share/isce2/` に保存しています。
