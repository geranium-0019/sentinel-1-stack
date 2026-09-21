# コンテナの実行環境検査

`scripts/check_ci.py` と `scripts/validate_stability.py` から実行します。
`environment/` 以下の調査用コードから、採用した検査をここに整理しています。
調査時のファイルは履歴として残しています。今後の CI 検査の修正先はこのディレクトリです。

`probe.py suite` は tests の全単体テスト、`probe.py codec` は TIFF 圧縮の往復検査です。
`xml_probe.py` は ASF import 後の XML 生成・読み取りの再現試験です。
`check_snaphu.py` と `check_resume.py` は合成データ・模擬ジョブを使います。
実際の SAR データの処理結果を保証する検査ではありません。
