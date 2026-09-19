# AIデザインSkillの導入

## AIに設定を依頼する場合

Codexへ、このGitHubリポジトリのURLと次の文章を送ってください。

> このGitHubリポジトリの `SETUP_WITH_AI.md` を読み、AIデザインSkillとFigmaの初回設定をしてください。私の操作が必要なところでは、何をすればよいか教えてください。

## 手動で設定する場合

1. このリポジトリを `~/.codex/skills/web-design-pro` へcloneします。
2. 管理者から個別に受け取ったトークンを、環境変数 `WEB_DESIGN_PRO_TOKEN` へ設定します。
3. `codex-config.example.toml` の内容を、既存の `~/.codex/config.toml` へ追記します。
4. Codexを完全に終了して再起動します。
5. `/mcp` で `web-design-pro` が接続済みであることを確認します。
6. CodexのFigmaプラグインを接続します。

トークンは `config.toml`、GitHub、CodexやChatGPTの会話、Notion、Figma、案件ファイル、スクリーンショットへ直接書かないでください。

画像の透過処理を使う端末ではPython 3とPillowが必要です。

```bash
python3 -c "from PIL import Image"
```

## 更新

Codexへ次のように依頼してください。

> AIデザインSkillをGitHubから最新版へ更新してください。

手動で更新する場合は、インストール先に未保存の変更がないことを確認してから実行します。

```bash
git -C ~/.codex/skills/web-design-pro pull --ff-only
```

更新後はCodexを再起動し、接続とバージョンを確認してください。
