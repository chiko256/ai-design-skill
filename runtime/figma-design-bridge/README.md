# Figma Design Bridge — 0.1.5

CodexからFigmaの編集可能なデザインを読み取り・作成・修正するローカルツールです。Figma MCPを使わず、FigmaのクラシックプラグインとPlugin APIを使います。デザインの判断はCodexが行います。

利用者は [START_HERE.md](START_HERE.md)、初回設定を行うCodexは [CODEX_SETUP.md](CODEX_SETUP.md) を読んでください。日常の操作は [AGENT_USAGE.md](AGENT_USAGE.md)、データ形式は [PROTOCOL.md](PROTOCOL.md) を参照します。

## 起動

- Mac: `Start Bridge.command`
- Windows: `Start Bridge.cmd`
- Codex・ターミナル: このフォルダーで `node bridge/start.mjs --json`

Node.js 18以上が必要です。Node本体は同梱していません。外部npmパッケージ、APIキー、Figma MCPは不要です。初回のみFigmaデスクトップへ`plugin/manifest.json`を開発プラグインとして登録します。

起動すると8文字の接続コードが発行されます。Figmaのプラグインに入力して「接続する」を押します。通常はCodexが起動・接続まで行います。接続後はFigmaと対象プラグインを開いたままにしてください。前面に表示し続ける必要はありません。

既存Bridgeは再利用します。他のBridgeや旧版がポート3849を使用中の場合、自動では停止しません。複数ファイルの接続は独立して維持され、ジョブはdocumentKeyで宛先を指定します。同じファイルへの書き込みは直列です。画面操作の順番は共通のUIロックで調整します。

## 接続と安全性

サーバーは127.0.0.1にだけbindします。長い接続キーは`.runtime/connection.json`に保存し、通常のHTTP APIは認証を要求します。短期コードの発行にも認証が必要です。コード交換だけはコードで認証し、5分間・1回限り・毎分20回までの試行制限を設けています。発行・交換は他ファイルの接続を切りません。コードを人に渡すとその人がこのローカルBridgeへ接続できるため、公開しないでください。

プラグインは接続キーを永続保存しません。Figma側を閉じた後の再接続はCodexがコードを再発行します。従来のconnection.json読み込みやキー入力は「詳細設定」に残しています。

`.runtime/`には秘密情報と案件の操作履歴が入ります。配布・Git保存の対象から除外してください。プラグインから任意JavaScript・シェルコマンドを実行する機能はありません。

## 対応範囲

読み取り、Auto Layout、編集可能な文字・画像・SVG・部品の構築、現在値を照合した部分修正、完成済みセクション内への部品追加・組み替え、構造検査、PNG/SVG書き出しに対応します。再送の重複防止と中断時の記録があります。既存の別案件やTOPを勝手に作り直しません。

部品追加・構成変更はプラグイン生成済みセクション内に限定します。任意の既存デザインの全構造変更、異なるセクションへの移動、既存ノードの削除、任意の変数やプロトタイプ操作には対応しません。

## 開発・検証

```sh
npm run check
npm test
```

テストはPlugin APIモックとローカルHTTPサーバーを使います。ローカル専用の案件fixtureがなければその追加テスト1件をskipします。Figmaの実際の描画・フォント・ネットワーク接続・書き出しは実機確認が必要です。WindowsのランチャーをMac上のテストだけで実機検証済みとは扱いません。

Bridge・エンジンversionは`0.1.5-connect.1`です。更新時は実行中の作業が完了してからBridgeと対象プラグインを再起動します。既存ノードとpluginDataは維持します。

manifestのIDは開発用です。Figma Communityへ公開する場合はFigma発行IDと公開用のネットワーク設定を用意し、公開・審査を行う必要があります。この配布版の作成は公開を含みません。

参考: [Figma Plugin API](https://developers.figma.com/docs/plugins/api/figma/)、[manifest / localhost](https://developers.figma.com/docs/plugins/manifest/)、[開発用プラグインの登録](https://help.figma.com/hc/en-us/articles/360042786733-Create-a-classic-plugin-for-development)
