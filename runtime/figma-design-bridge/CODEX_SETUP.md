# Codex向け初回設定

これはプラグインの設定手順。制作工程のSkillではない。ユーザーがこのフォルダーでの設定・接続を依頼したときに使用する。

## 保存場所と実行環境

この文書があるフォルダーを`pluginRoot`とする。Mac固有の絶対パスを流用しない。Node.js 18以上を確認し、`node`がPATHにあればそのまま使う。Codexの同梱ランタイムを利用できる場合は、利用可能なワークスペース依存ランタイムのパスをツールで確認し、確認済みNodeの絶対パスで実行してよい。存在を推測したパス、外部npmパッケージ、管理者権限でのインストールは不要。

Nodeの自動検出ができなければ、ユーザーに理由を具体的に伝える。ランチャーに任意のNodeを渡す場合は環境変数`FIGMA_BRIDGE_NODE`を使う。Windows PowerShellでは`& $nodePath ./bridge/start.mjs --json`のように実行する。OSのセキュリティ確認を無効化・迂回しない。

## 初回登録と接続

1. `AGENT_USAGE.md`を読む。最初に`node bridge/start.mjs --json`でBridgeを起動・再利用する。既定ポートは3849。旧版や他フォルダーのBridgeが使用中なら、他案件の実行状況を確認するまで停止しない。
2. 認証済みCLIで`status`を確認する。対象が接続済みなら設定を繰り返さない。
3. 画面操作の前に共通BridgeのUIロックを取得し、各段階でrenewする。
4. Git配布の接続用Skillに同梱された本体を使う場合は、そのSkillの`scripts/figma-bridge.mjs prepare`で作成・更新したローカルmanifestを登録対象にする。Git同梱元のファイルは編集しない。Figmaデスクトップの対象ファイルを開く。未登録の場合だけ、Plugins → Development → Import plugin from manifest… から`pluginRoot/plugin/manifest.json`を読み込む。Figma Design Bridgeという表示名を確認する。登録時にローカルIDが拒否された場合は、FigmaのNew pluginでCustom UIを作成し、その環境で発行されたIDを登録対象のローカルmanifestへ設定する。Git同梱版では`.runtime/plugin/manifest.json`だけを変更し、次回prepareはそのIDを保持する。IDを勝手に作ったり、別の公開プラグインのIDを使ったりしない。
5. 対象ファイルでプラグインを起動する。`node bridge/cli.mjs pair`で新しい短期コードを発行し、通常画面の「Codexの接続コード」に入力して「接続する」を押す。connection.jsonをファイル選択する必要はない。コードは5分有効・1回限り。会話や成果物に保存しない。UIには接続キーを永続保存しない。
6. `status`の対象fileName・pageId・documentKeyを確認し、そのdocumentKeyを指定した`context`で接続先を再確認する。ここまでは読み取り。依頼のないデザイン変更を接続テストとして行わない。
7. UIロックをreleaseする。通常の起動・接続を「接続できたら教えてください」という質問に置き換えず、必要な操作をCodexで進める。Computer Useがない、ログイン等で人の操作が必要、OSの権限確認が必要な場合は、その理由と最小限の操作を説明する。

## 運用

利用者には毎回のキー入力やファイル選択を依頼しない。対象URLと制作内容を受け、接続済みならCLIを再利用する。別ファイルのプラグインは停止しない。閉じたプラグインはFigmaの画面から起動する必要がある。この版にはOS常駐アプリ、PC起動時の自動起動、Figmaを開かずに編集する機能はない。

Bridgeはバックグラウンドで動作する。停止が必要なら、自分が起動したプロセスであることと、`status.jobs`および.runtime/jobs内にqueued/runningがないことを確認してから止める。全nodeプロセスをまとめて停止しない。更新時も実行中ジョブを中断しない。

実際のレイアウト編集は`PROTOCOL.md`と`AGENT_USAGE.md`に従う。APIから成功が返っただけで完成とせず、対象を再読取し、書き出し画像も確認する。
