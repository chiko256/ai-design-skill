# AIデザインSkillの導入

## AIに設定を依頼する場合

Codexへ、このGitHubリポジトリのURLと次の文章を送ってください。

> このGitHubリポジトリの `SETUP_WITH_AI.md` を読み、AIデザインSkillとFigmaの初回設定をしてください。私の操作が必要なところでは、何をすればよいか教えてください。

## 手動で設定する場合

1. このリポジトリを `~/.codex/skills/web-design-pro` へcloneします。
2. 管理者から個別に受け取ったトークンを、環境変数 `WEB_DESIGN_PRO_TOKEN` へ設定します。macOSでは、下記の「macOSでトークンを永続化する」を実施します。
3. `codex-config.example.toml` の内容を、既存の `~/.codex/config.toml` へ追記します。
4. Codexを完全に終了して再起動します。
5. `/mcp` で `web-design-pro` が接続済みであることを確認します。
6. CodexのFigmaプラグインを接続します。

トークンは `config.toml`、GitHub、CodexやChatGPTの会話、Notion、Figma、案件ファイル、スクリーンショットへ直接書かないでください。

## macOSでトークンを永続化する

`launchctl setenv`だけで設定した環境変数は、Macの再起動やログアウトで消える。macOSでは、トークンをログインキーチェーンへ保存し、LaunchAgentでログイン時に自動設定する。

リポジトリを標準の場所へ配置したあと、ターミナルで次を実行する。

```bash
~/.codex/skills/web-design-pro/scripts/setup-macos-auth.sh
```

入力待ちになったら、管理者から受け取った契約者専用トークンを貼り付けてEnterを押す。入力値は画面とコマンド履歴へ表示されない。正常時は次の結果になる。

```text
ai-design-auth:configured
```

この設定では、トークン自体をLaunchAgent、設定ファイル、シェル設定ファイルへ書き込まない。Codexを完全に終了して起動し直したあと、`/mcp`で接続を確認する。

CodexをmacOSのログイン項目から自動起動している場合、LaunchAgentより先に起動すると環境変数を受け取れないことがある。その場合はCodexを完全終了して起動し直す。

再起動後に接続エラーが出る場合は、次の診断を実行する。表示されるのは状態だけで、トークンの値は表示されない。

```bash
~/.codex/skills/web-design-pro/scripts/check-macos-auth.sh
```

以前の設定スクリプトで`ai-design-auth:configured`と表示されたことがある場合は、リポジトリを更新し、保存済みトークンを使ってLaunchAgentを再登録できる。

```bash
git -C ~/.codex/skills/web-design-pro pull --ff-only
~/.codex/skills/web-design-pro/scripts/setup-macos-auth.sh --use-existing-token
```

再登録後にCodexを完全終了して起動し直す。キーチェーンの読み込みに失敗する場合だけ、引数なしのセットアップスクリプトを実行し、契約者専用トークンを本人が再入力する。

`launchctl getenv WEB_DESIGN_PRO_TOKEN`をそのまま実行するとトークンが表示されるため、確認目的で実行しない。

画像の透過処理を使う端末ではPython 3とPillowが必要です。

```bash
python3 -c "from PIL import Image"
```

## 更新

制作開始時にGitHubの最新版を確認し、更新があれば自動で反映します。通常は利用者による更新操作は不要です。

自動更新ができない場合だけ、インストール先に未保存の変更がないことを確認してから手動で実行します。

```bash
git -C ~/.codex/skills/web-design-pro pull --ff-only
```

更新後はCodexを再起動し、接続とバージョンを確認してください。
