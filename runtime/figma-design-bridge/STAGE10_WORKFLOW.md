# 工程10を自作プラグインで実行する手順

このファイルは接続方法の補足です。案件の工程10ルール、DESIGN.md、原稿、素材handoffを正本にします。既存スキルファイルは変更していません。このrunではFigma MCPを使わず、bridge/cli.mjs経由で操作します。

## 開始

1. `preflight` でanalysis.json、materials.json、採用画像、実素材の存在を確認。
2. モックアップを画像として確認し、DESIGN.mdとtexts.mdを読む。
3. `status` と `context` で接続先を確認。既存targetの `inspect` と画像書き出しで、保存記録と現在のキャンバスが一致するか確認。
4. `fonts` で必要なfamilyとstyleを確認。
5. 新しいrunの記録を作る。原本の過去の完了記録を引き継いで合格にしない。

```sh
node bridge/cli.mjs preflight PROJECT_DIR fv
node bridge/workflow.mjs init PROJECT_DIR RUN_DIR fv
```

## 01: セクションごとに組み立てる

1. 1セクション分のbuildSection指示を作る。全elementとrender_methodを対応させ、紙・写真などは実素材を使用する。
2. セクションrootのpaddingTop/paddingBottomにDESIGN.mdの値を指定。rootは縦Auto Layout / 高さAUTO、wrapper gapは0。PC FVのminHeightは900以上。
3. 指定順の前のセクションが検査済みかrun記録で確認してから、1セクションだけsubmitする。
4. 返されたrootId、wrapperId、refs、elementMapを記録する。
5. `validateSection` と `exportNode` を実行。書き出した画像をAIが見て、文言、改行、素材、構図、重なり、FVのナビ以外の中央配置を照合。
6. 修正があれば対象のみinspectし、expected付きpatchを実行して再検査・再export。
7. 検査と目視確認を記録した後に、次のセクションへ進む。

```sh
node bridge/workflow.mjs record RUN_DIR BUILD_JOB_ID
node bridge/workflow.mjs record RUN_DIR VALIDATION_JOB_ID
node bridge/workflow.mjs record RUN_DIR EXPORT_JOB_ID
node bridge/workflow.mjs review RUN_DIR review.json
```

review.json:

```json
{
  "sectionId":"fv",
  "validationJobId":"実際の検査ジョブID",
  "exportJobId":"実際の最新exportジョブID",
  "reviewer":"実際に確認したAI担当名",
  "visualPassed":true,
  "checks":["実際に確認した結果だけを記入"],
  "commonValuesVerified":false
}
```

構造検査成功、同じnodeのexport、その後に別の書き込みがないことを補助CLIが確認します。ユーザーのFigma内手動編集はローカルジョブ履歴には含まれないため、レビュー直前に現状を再取得し、手動編集があれば最新画像で確認し直してください。

## 02: 共通値を反映する

- 工程01の完了後、実際の文字と余白を取得して共通化する。
- DESIGN.mdのH1/H2の対象別採用値を維持。MAX値を一律適用しない。
- H1、H2/H3、本文、補助文字の区分と言語に合う%行間を使用。
- ナビ15px以上、15px以下は指定ゴシック。H1の左端をwrapper基準で100px以上にする。
- FV外のCTA以外の共通ボタンはCOMPONENTとINSTANCEで作る。今回のFV検証では適用対象外。
- 共通化前に対象だけinspectし、文字設定や余白をpatchNodesで反映。Text StyleはtextStylesで保存・適用。
- セクション上下余白を保ち、折り返しと高さを再確認。全対象を最後に検査・exportし、commonValuesVerified:trueの目視レビューを記録。
- `finish` でstage10_handoffを保存。工程11は未完了のまま次担当へ渡す。

```sh
node bridge/workflow.mjs finish RUN_DIR
```

runのanalysis.jsonは新しい検証成果物です。元案件へ統合する場合は、新しいnode ID、変更と確認結果だけを統合し、工程09や過去の履歴を古いコピーで置換しないでください。

## コンテキストを抑える運用

- 最初にページの子と対象セクションの浅い構造を取得。必要な枝だけ追加で読む。
- 変更予定の属性だけfieldsに指定。
- 画像bytesやbase64はシェル出力に出さない。画像ファイルをview_imageで確認。
- 書き込みは1セクション単位、失敗時は結果と対象だけ再読取。
- 全体の構造情報を何度も返す代わりに、validateSectionの問題一覧を使う。
- コンテキスト削減率はまだ未計測。MCPとの同条件比較後に評価する。
