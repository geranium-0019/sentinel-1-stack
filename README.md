# sentinel-1-stack

Sentinel-1 IW SLCをISCE2で処理するDocker環境と操作ツールです。
データ取得から干渉解析・アンラップ・GeoTIFF出力まで実行できます。

## 始め方

Linux x86_64、Docker、Bash、ホスト側のPython 3.9以上が必要です。
WindowsではWSL2とDocker Desktopを使います。

好きな場所でソースを取得します。

```bash
git clone git@github.com:geranium-0019/sentinel-1-stack.git
cd sentinel-1-stack
```

GitHubへのSSH接続設定が必要です。Dockerイメージはレジストリ未公開です。
配布されたイメージがない場合は、ここでビルドします。

```bash
docker build --platform linux/amd64 -t sentinel-1-stack:dev .
```

次に **[操作マニュアル](docs/操作マニュアル.md)** に沿って進めてください。
上のコマンドでビルドした場合、マニュアルの `IMAGE` は `sentinel-1-stack:dev` にします。

## 処理の流れ

作業ディレクトリを決める → ASFのJSONを置く → 設定する → 以下を順に実行します。

`init` → `download` → `orbit` → `dem` → `prepare` → `run` → `export`

データ・結果は指定した作業ディレクトリに保存します。外部ディスクの `/mnt/…` も使えます。

## 詳しい説明

- [操作マニュアル](docs/操作マニュアル.md)：最初から順に実行する手順
- [詳細リファレンス](docs/reference.md)：認証・オプション・エラー対応・テスト
- [配布イメージの使用手順](docs/releases/0.1.0-rc1.md)：イメージファイルの読み込み方

現在は個人利用で検証中です。実データの処理実績はありますが、反復テストでの異常終了が未解決です。
詳細は [調査記録](environment/diagnosis-20260921/REPORT.md) を参照してください。
