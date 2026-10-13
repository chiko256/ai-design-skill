# Figma Design Bridge 共通操作

工程10と下層ページ制作で同じ本体を使う。デザイン判断、素材の条件、工程記録、完成条件は呼び出し元のSkillに従う。ここでは実行方法だけを扱う。

## 本体と接続

この文書があるフォルダーを `pluginRoot` とする。呼び出し元が明示した本体がある場合は、それを使う。`PROTOCOL.md`と必要な操作の実装を確認する。Node.js 18以上を使う。

Figmaには `plugin/manifest.json` を開発プラグインとして登録する。表示名は「Figma Design Bridge」。既存登録は互換パス経由でも同じ本体を参照する。

Bridgeの起動・再利用には `node bridge/start.mjs --json` を本体フォルダーで実行する。初回設定はCODEX_SETUP.mdを参照する。既定ポートは3849、接続先はlocalhostのみ。`--state`を指定した場合は以降のCLIも同じstateDirを使う。既定stateDirは `pluginRoot/.runtime`。

## 接続はCodexが行う

通常の起動・接続・ファイル切替は、依頼された制作に必要な作業としてCodexが行う。「別案件を切断して対象へ接続してください」「接続できたら教えてください」という質問へ置き換えない。ユーザーが自分で接続すると明示した場合や、画面操作ツールが使えない場合、ログイン等で実際にユーザーの操作が必要な場合だけ、必要な操作と理由を具体的に伝える。アプリや実行環境が要求する権限確認は省略しない。

最初にCLIのstatusを読む。`clients`のうちconnected=trueの接続を見て、対象のfileName・pageId・documentKeyを照合する。複数接続時の`client=null`は未接続を意味しない。複数候補があれば文書を確認して選び、先頭を勝手に採用しない。接続済みなら画面操作なしで再利用する。

0.1.4以降は複数ファイルを同時に接続できる。他ファイルの接続を停止しない。対象が未接続なら、そのファイルのタブで「Figma Design Bridge」を起動し、`node bridge/cli.mjs pair`で接続コードを発行し、通常画面に入力して接続する。コードは5分間・1回限りで、会話や成果物に保存しない。従来のconnection.json読み込みは詳細設定に残る。既存の別案件のプラグインを閉じたり、全接続を切り替えたりしない。タブが閉じられていれば指定URLから対象を開く。プラグインを起動する画面操作は必要だが、接続後の制作はCLIを使う。

画面操作の直前に共通BridgeのUIロックを取得する。`--owner`には現在のチャットIDまたは今回の接続作業で固定する一意なID、`--target`には対象Figma URLを指定する。返されたlease.idを保持する。ロック中ならstatusで担当と期限を確認し、画面へ割り込まず、独立した準備を進めて短い間隔で再試行する。接続作業そのものをユーザーに任せる質問で止めない。ロックを取れたら毎回最新の画面を取得し、各画面操作の前にrenewする。renewできなければ画面操作を中断し、再取得してから画面を読み直す。完了・失敗時にreleaseする。ロックの期限は既定120秒で、放置されたロックは回復できる。

このUIロックは手順に従うCodex同士の協調用。Figma自体の操作を物理的に禁止するものではない。ユーザーが画面を操作した場合も、最新の状態を取り直す。

接続キーを会話、成果物、ログへ出さない。status、続いてcontextと対象IDのinspectで制作先を確認する。documentKeyはFigma URLのfile keyではない。ジョブには必ず確認済みdocumentKeyとpageIdを明示する。対象を確認できない状態で書き込まない。

```sh
node bridge/start.mjs --json
node bridge/cli.mjs pair
node bridge/cli.mjs status
node bridge/cli.mjs status --document DOCUMENT_KEY
node bridge/cli.mjs ui-lock acquire --owner CHAT_ID --target FIGMA_URL
node bridge/cli.mjs ui-lock renew --owner CHAT_ID --lease LEASE_ID
node bridge/cli.mjs ui-lock release --owner CHAT_ID --lease LEASE_ID
node bridge/cli.mjs submit /ABS/PROJECT/work/job.json --document DOCUMENT_KEY --wait 30
node bridge/cli.mjs result JOB_ID
```

## 操作と確認

- `context / inspect / fonts / inspectResources`: 読み取り。
- `buildSection`: セクションと部品を作成。
- `patchNodes`: 最新値をexpectedに入れて変更。
- `insertNodes / rearrangeNodes`: 対応版で同一の完成済みsection内に追加・構成変更。
- `exportNode`: 書き出して画像表示ツールで実物を確認。

同じファイルへの書き込みは直列に行う。別ファイルのキューは独立しており、ジョブに指定したdocumentKeyの接続だけが受信する。queued/runningなら同じjob IDの結果を取得する。失敗・中断・partialFailureでは対象を再取得してから修正し、実行済みか不明な操作を再送しない。対応範囲やpayloadはPROTOCOL.mdを正本とする。

操作JSONと結果の控え、確認用画像は案件のworkへ保存する。完成物だけ案件のoutputsへ保存する。共有本体に案件原稿や新しい素材を追加しない。既存examplesは自動テスト用fixtureとして保持している。

Figmaへの送信成功や自動テストの通過だけで完成としない。対象nodeと書き出し画像を確認する。既存TOPや他案件のnodeは変更しない。
