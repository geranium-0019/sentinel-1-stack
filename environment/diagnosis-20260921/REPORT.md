# rc1 異常終了の調査（2026-09-21）

## 結論

異常終了を別の場所でも再現した。原因のライブラリ／処理は未確定で、修正済みとは判定しない。
rc1 の不合格を維持する。候補イメージとNumPy 1.26.4は変更していない。

当初は引数解析中の `shutil.get_terminal_size` で SIGSEGV。
今回は `PYTHONMALLOC=debug` で全体検査の3回目に、GCが
`type_traverse() called on non-heap type 'MagicMock'` を検出し、終了139となった。
これはPython内部オブジェクトの整合性異常を示すが、破壊した処理を示すものではない。
単独テストの成功、デバッガ下の成功をもって解決とは扱わない。

## 切り分け結果

| 条件 | 結果 |
|---|---|
| rc1の `/usr/local` と固定した公式Pythonイメージのファイルハッシュ比較 | 元ファイルの変更・欠落0。追加は起動用シェル7個のみ |
| 配布tarの全レイヤーを設定内diff IDと比較 | 24/24一致 |
| 公式Pythonのみ：argparse/XML/mock各10,000ループ、20個の新規コンテナ | 全成功 |
| rc1：標準ライブラリのみ／ASF import後／CLI・GDAL・Shapely import後に上記ループ | 各1回成功 |
| rc1：軌道テスト単独 | 新規コンテナ20回成功 |
| rc1：prepare＋runnerテスト、PYTHONMALLOC=debug | 新規コンテナ20回成功 |
| rc1：全114テスト、PYTHONMALLOC=debug | 2回成功、3回目で異常終了。以後停止 |
| rc1：DEM・export・unwrapped_exportを除いた全体テスト、debug allocator | 新規コンテナ10回成功 |
| rc1：上記3モジュールのテスト＋標準ライブラリ反復、debug allocator | 新規コンテナ10回成功 |
| 調査用イメージ：gdb下で同一Pythonプロセス内の全体テスト40回 | 全成功。再現なし |
| 調査用イメージ：gdb＋debug allocator、新規コンテナの全体テスト10回 | 全成功。再現なし |
| 調査用イメージ：Valgrind＋PYTHONMALLOC=malloc、全体テスト | 114件は成功、検査は終了99。未初期化値1740件／10文脈。Invalid read/write/freeの報告なし |
| 同条件で標準ライブラリの読み込み＋少数の16進数変換 | Valgrind 0件 |
| 同条件で公式Python3.11.10＋NumPy importのみ | Valgrind 未初期化値300件／8文脈 |
| 同条件で公式Python3.11.10、NumPyを使わない整数の10進文字列往復 | 計算結果は正しく、Valgrind 未初期化値2件／2文脈 |
| 同条件でConda Python3.11.14＋同じNumPy import | Valgrind 0件 |
| rc1内のConda Python3.11.14へLD_LIBRARY_PATHも合わせて全体テスト | 初回でXMLのAttributeError 2件。以後停止 |

Valgrindの警告はNumPyを使わない整数変換でも再現したため、NumPyのバグや
NumPy 1.x／2.xの問題と断定しない。整数変換の警告とSIGSEGVの因果も未確定。
Conda Pythonへ切り替えるだけでも解決しない。XMLノードが期待と異なる型となるエラーが出た。

調査用 `rc1-debug` / `rc1-memcheck` はデバッガを追加した別イメージ。
gdb導入時は0パッケージ更新・52追加で、候補そのものにはインストールしていない。
gdbのバッチ終了1は、正常終了後にレジスタ取得コマンドが失敗した結果であり、
被検査Pythonの失敗ではない。ログの `exited normally` とテスト完走を確認した。
Valgrindの終了99は警告を検出した結果であり、検査合格とは扱わない。

## ホスト側の観測と限界

- WSL 2.6.3.0、Linux 6.6.87.2、Intel i9-14900HX。
- 元の異常終了時刻にカーネルもPythonのSIGSEGVを記録。OOM killの記録はない。
- 別時刻に `ls` の実行時、カーネルのmemcg処理でpreemption警告が1件。
  今回のPython異常終了との因果は未確定。
- Windowsの過去7日間のWHEA記録は1762件、全てID17のPCI Express Root Portの訂正済みエラー。
  最新は2026-09-19 08:31:45 UTCで、今回のクラッシュ時刻と一致しない。
  CPUやRAMの故障を証明する記録ではなく、これを原因と断定しない。
- 利用可能なのは現在のPCのみ。他ホストとの比較は未実施。
- WSL／Dockerの再起動、BIOS変更、Windows設定変更、実データの再処理は行っていない。

## 保存先と次の調査

生ログ・JSON・ホスト情報は `build/rc1-diagnosis/`。調査用コードはこのディレクトリ。
過去の配布セット `dist/0.1.0-rc1/` と失敗記録・チェックサムは変更していない。

次に必要なのは、全体テストの処理順・組み合わせを減らして、再現しやすい最小ケースを作ること。
そのケースでライブラリを一つずつ変更し、発生しなくなる条件を確認する。
現状では自作アプリのどの行を修正すべきかの根拠がなく、テストの分割や回数削減で
配布基準を緩める変更は行わない。

調査手段の資料：
[Python faulthandler](https://docs.python.org/ja/3.11/library/faulthandler.html)、
[CPythonのGDBガイド](https://github.com/python/cpython/blob/main/Doc/howto/gdb_helpers.rst)。
これらは調査手段の資料であり、今回の原因を裏付ける資料ではない。
