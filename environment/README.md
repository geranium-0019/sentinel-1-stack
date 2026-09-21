# Docker 環境の管理

通常のビルドはリポジトリ直下の `Dockerfile` を使います。
Linux x86_64（`linux/amd64`）用です。

- `linux-64.explicit.txt`: Conda の実際のビルド入力。版・ビルド番号・配布 URL・チェックサムを固定。
- `download-requirements.txt`: ASF ダウンロード用の追加 Python パッケージの固定版。
- `runtime-entrypoint.sh`: Micromamba の環境設定を読み込んだ後、公式 Python を先頭に選択。
- `environment.yml`: 依存の概要。編集しても通常の Docker ビルドには反映されません。
- `stabilization/`: 比較試験のレシピ、ログ、採用判断の履歴。通常ビルドはこのディレクトリを参照しません。

実行用 Python は Dockerfile の公式イメージ digest により 3.11.10 に固定しています。
Conda の Python はビルドと依存関係のために残しています。
Micromamba の entrypoint を省略すると GDAL/PROJ のデータパスが失われるため、
独自起動でもイメージの entrypoint を維持してください。

NumPy は 1.26.4 を維持します。依存を変更するときは、隔離した環境で解決した
explicit lock を作り、Dockerfile のベース digest と合わせてレビューします。
その後、依存検査、XML100回、全体テスト10回、実データの処理・中断再開・出力検査、
独立2ビルドの構成一致を確認します。手順と検証範囲は `stabilization/REPORT.md` を参照してください。
