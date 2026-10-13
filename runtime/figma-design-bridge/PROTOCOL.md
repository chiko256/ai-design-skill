# 操作データ v1

Figma標準Plugin APIの値を使います。色は0〜1、行間は `{ "unit": "PERCENT", "value": 160 }`。任意JavaScriptを送る機能はありません。

## context / fonts / inspect

```json
{"op":"context"}
```

```json
{"op":"fonts","query":"Noto Sans JP","offset":0,"limit":50}
```

```json
{"op":"inspect","pageId":"0:1","nodeId":"17:2","depth":2,"limit":80,"offset":0,"fields":["width","height","characters","fontName","fontSize","lineHeight","paddingTop","paddingBottom"]}
```

`inspect` は最大深さ8、最大500件。`types:["TEXT"]` などで絞れます。次のページは `nextOffset` を使用。必要な範囲に絞り、ファイル全体の走査やbase64をAIへ返さないでください。`characters` は要求した場合だけ返します。

## buildSection

ローカル専用の入力例は `examples/tokyo-spice/build-fv.json`（配布ZIPには案件素材を含めない）。1ジョブ=1セクション。`plan.projectId` と `plan.sectionId` はrun内で固定します。新規wrapperは縦Auto Layout / gap 0 / 高さAUTOで生成。続くセクションも同じprojectIdなら同じwrapperへ追加。`parentId` で既存wrapperを指定する場合も、この構造条件が必要です。

`plan.tree` のノード:

- `key`: セクション内で一意の安定キー。
- `type`: FRAME / TEXT / RECTANGLE / ELLIPSE / LINE / VECTOR / COMPONENT / INSTANCE / SVG。
- `props`: code.jsのSAFE_PROPSで許可するFigmaプロパティ。HUG/FILLは実際の親子構造に従う。
- `children`: FRAMEまたはCOMPONENTだけ。順番が重なり順・Auto Layout順になります。
- `role`, `language`: 例 h1 / h2 / body / nav、ja / en。
- `vectorPaths`: VECTORの採用された単純パス。
- `image`: `{ "assetId":"photo", "scaleMode":"FILL" }`。画像内容をベクターに描き直しません。
- SVGは `assetId` で採用SVGを指定。`svgColor:"#ffffff"` でcurrentColorだけを置換でき、形状は保持します。
- INSTANCEは `componentRef`（先に作ったCOMPONENTのkey）、`componentId`、`componentKey`のいずれか。必要なら `componentProperties` を指定。
- TEXTの `textRuns`: `[{"start":0,"end":4,"props":{"fills":[...]}}]`。fills / fontName / fontSize / letterSpacingを指定可能。

`assets` はCLI入力では `{"photo":{"path":"assets/photo.png"}}`。CLIが画像bytesまたはSVG本文へ展開し、計画+素材のSHA-256を `planDigest` として付けます。手動インポートは `pack` 済みデータを使います。

同じprojectId/sectionIdが存在すると、同じdigestかつ完成済みなら既存IDを返します。異なるdigestや作成途中のセクションは止めます。通常の例外では今回作ったノードだけを削除します。プロセス強制終了時は自動ロールバックを保証しません。

`elementMap` は `{id,ref,renderMethod,assetId?}` の配列。1要素が複数nodeに対応する場合は同じidの複数行を指定。素材記録の全elementを網羅してください。

### 欠落した外枠の復元（エンジン0.1.1）

`buildSection` に `repair:{wrapperId,missingRootId,children:[{id,expected}]}` を追加し、`plan.parentId` を既存wrapper IDに固定します。通常の再構築とは別の処理です。既存の文字・画像・ノードIDを保って、外枠FRAMEだけを作成します。expectedには各直接子のwidth、height、layoutMode、primaryAxisSizingMode、counterAxisSizingModeが必要です。

実行前にcontextのversionが0.1.1以降の対応版であることと、最新のwrapper状態を確認してください。旧エンジンには復元処理がないため送信してはいけません。元の外枠がまだある場合、wrapperに別の子がある場合、ノード数・安定キー・sectionKey・順序・expectedが異なる場合は停止します。復元後は新rootIdで検査・書き出し・目視レビューをやり直します。

## patchNodes

```json
{"op":"patchNodes","pageId":"0:1","patches":[{"id":"20:10","expected":{"fontSize":72},"props":{"fontSize":72}}]}
```

`expected` に変更する全プロパティの現在値を入れます。文字、色、余白など変更対象だけ指定します。全対象を事前照合し、フォント読込後にも再確認します。途中のAPI失敗は `partialFailure` と変更済みノードを返します。自動で再送しないでください。

## 部品追加・構成変更（0.1.3-layout-edit.1）

Figma内のプラグインとBridgeを更新・再起動し、`context.version` を確認してから使用してください。以下は**形式の例**です。IDとexpectedは最新のinspect結果で置き換えます。実際のFigmaへの変更は明示的にsubmitしたときだけ行われます。

### insertNodes

完成済みのプラグイン生成セクション内のFRAMEに、1〜50個のツリーを追加します。入れ子込みで最大1500計画ノード。新しい文字、写真、斜線、ベクター、SVG、フレーム、コンポーネント、インスタンスを扱い、既存IDは保ちます。

```json
{
  "op": "insertNodes",
  "pageId": "PAGE_ID",
  "key": "add-photo-caption-v1",
  "parentId": "FRAME_ID",
  "index": 1,
  "expected": {"childIds": ["EXISTING_ID"], "width": 600, "height": 400, "layoutMode": "NONE"},
  "nodes": [{
    "key": "photo-caption",
    "type": "TEXT",
    "role": "body",
    "language": "ja",
    "props": {
      "name": "写真の説明", "characters": "写真で外壁の状態を確認します。",
      "fontName": {"family": "Noto Sans JP", "style": "Regular"},
      "fontSize": 22, "lineHeight": {"unit": "PERCENT", "value": 160},
      "textAutoResize": "HEIGHT", "width": 500, "x": 24, "y": 300
    }
  }]
}
```

`index` は現在の直接子の0〜child数。通常のAuto Layoutへの追加は親の高さが変わる場合があります。Auto Layout内で重ねる要素には `layoutPositioning:"ABSOLUTE"` と座標を指定します。`nodes` の形式・画像素材のpackはbuildSectionのtreeと共通です。既存セクション内で同じノードkeyは使えません。

失敗時は今回追加したノードだけを削除します。削除にも失敗した場合は `partialFailure`、`rollbackError`、`inspectNodeIds` を返します。再読取前の自動再送は禁止です。

### rearrangeNodes

影響するすべての親FRAMEについて、現在の子順序と変更後の子順序を指定します。各親は同じセクション内である必要があります。**現在と変更後の子IDの集合は完全に同一**で、欠落・追加・重複・循環する親子関係は書き込み前に拒否します。枠でまとめたい場合はinsertNodesで空FRAMEを先に作り、inspect後に既存部品を移動します。

```json
{
  "op": "rearrangeNodes",
  "pageId": "PAGE_ID",
  "key": "wrap-photo-v1",
  "parents": [
    {"id": "SECTION_ID", "expected": {"childIds": ["PHOTO_ID", "GROUP_ID"], "width": 1440, "height": 900, "layoutMode": "VERTICAL"}, "childIds": ["GROUP_ID"]},
    {"id": "GROUP_ID", "expected": {"childIds": [], "width": 700, "height": 500, "layoutMode": "NONE"}, "childIds": ["PHOTO_ID"]}
  ],
  "nodes": [{
    "id": "PHOTO_ID", "expected": {"parentId": "SECTION_ID", "x": 64, "y": 108},
    "props": {"x": 0, "y": 0}
  }]
}
```

`parents` は最大50件、直接子IDの集合は最大500件。`nodes` は移動と同時に変えたいプロパティだけを指定します。各ノードの `expected.parentId` と、変更する各プロパティの現在値が必要です。自由配置の親へ移す、またはABSOLUTE状態で親を変える場合は、移動先の親を基準とする `x/y` を必ず指定します。通常のAuto Layoutでは流れに応じて位置が変わります。親変更時の見た目の座標保持を自動で推測しません。

親を含む構成変更は変更後の親子関係の外側から適用し、レイアウト変更は子の座標変更より先に行います。フォント読込後にもすべての親・変更値を照合します。

途中のAPI失敗は `partialFailure:true` と `inspectNodeIds` を返し、完了扱いにしません。既存ノードの削除や、手動編集を巻き戻す自動ロールバックは行いません。最新状態をinspectして、新しいkeyの修正計画を作ってください。

### 共通のpack・再送・対象制限

- CLIのsubmitまたはpackが、操作全体と実際の素材bytesから `mutationDigest` を計算します。pack後のJSONを手修正せず、元のJSONを直してpackし直します。
- セクション内で操作ごとに一意の `key`（1〜128文字）を使います。同じkey・digestの再送は既存結果を返し、追加や移動を繰り返しません。変更後の構造・指定プロパティが手動編集された場合は停止し、上書きしません。
- digestが違う同じkey、または中断・失敗した同じkeyは停止します。自動再試行の代わりに、読取と再計画を行います。
- 操作記録はセクションのpluginDataへ保存します。画像bytesを保存せず、記録は最大128件・セクションメタデータ全体90KBまで。上限時は処理を停止します。追加の記録保存失敗は新規ノードの削除対象、構成変更の記録保存失敗はpartialFailureです。
- 既存のソースTOP、セクションwrapper、別セクションへの移動、インスタンス内部の構造変更は対象外です。インスタンス自体の並べ替えはできます。
- 結果は `needsVisualReview:true` です。構造が正しくても、実際の改行・トリミング・重なりはexportして確認します。

## validateSection / exportNode

```json
{"op":"validateSection","pageId":"0:1","nodeId":"SECTION_ID","wrapperId":"WRAPPER_ID"}
```

作成時のdesign指定を使います。既存セクションなら `rules` を明示。`structuralPassed` は自動検査対象だけの結果です。

```json
{"op":"exportNode","pageId":"0:1","nodeId":"SECTION_ID","format":"PNG","scale":1}
```

Bridgeが画像を保存し、結果には `exportPath` とSHA-256を返します。AIが実際にその画像を見て評価します。

## textStyles

`styles` に `{name,fontName,fontSize,lineHeight,letterSpacing?,paragraphSpacing?,targets}` を指定します。名前は `Stage10/案件/役割/採用値` のように `Stage10/` で始めます。同名Styleが違う値なら停止するため、別案件やユーザーのStyleを上書きしません。

各targetは `{id,expected}`。expectedには `fontName,fontSize,lineHeight,letterSpacing,paragraphSpacing,paragraphIndent,textCase,textDecoration,textStyleId` の現在値を入れます。共通化前に対象TEXTをこの項目でinspectしてください。スタイル適用後は値と改行を再検査し、最新画像を書き出します。

APIエラー後は、追加されたStyleや途中まで適用したTEXTが残る場合があります。対象を再取得してから再開します。

## 対応の限界

自由なページ構成はtreeとpropsで指定できますが、Figmaの全APIを公開しているわけではありません。未知のプロパティは黙って無視せずエラーにします。必要な操作は試験を加えて拡張します。自動の意味理解・デザイン判断・DESIGN.mdの解釈はAI側が担当します。

### appendDecorations (isolated lower-page test extension)

Adds 1–20 individual raster RECTANGLE nodes to an existing Stage10 section. Requires `parentId`, `expected: {childIds, width, height, layoutMode}`, `decorations` and packed raster `assets`. Every decoration requires a unique key, explicit geometry and ABSOLUTE positioning. The CLI hashes its parent, specs and asset bytes into `decorationDigest`.

The operation preserves existing node IDs and section sizing. A matching repeated delivery returns the original decoration IDs. Changed geometry, duplicate/incomplete keys, a different digest or changed parent children cause a conflict. Allocation failures remove only newly allocated nodes. It does not execute arbitrary JavaScript or replace the page with a raster image.

Final section 03 uses 80px bottom padding from the approved finished source. The original build plan remains historical; use `jobs/finish-validate-03.json` from the verification evidence for the final explicit validation rules.

## 3-project isolated extension: componentTextProperties / componentVariants

These operations are restricted to exact test file names and plugin-created nodes (`meta.key`). They never evaluate JavaScript. Source files are rejected before mutation.

`componentTextProperties`: `{op, pageId, componentId, key, expected:{componentPropertyDefinitions}, bindings:[{nodeId, name, expected:{characters,componentPropertyReferences}}]}`. Provides 1–20 ASCII-named TEXT properties on one Main COMPONENT. Every target must be a descendant TEXT without an existing characters binding. All targets are preflighted before property creation. Failures attempt to remove only newly added properties and restore their references; inspect when `partialFailure` is returned. Repeating the same key/spec returns existing property IDs without duplicates.

`componentVariants`: `{op,pageId,parentId,key,name:"Lower/H2-A",expected:{childIds},variants:[{id,name:"Mode=Light",expected:{name,width,height,componentPropertyDefinitions}},{id,name:"Mode=Dark",expected:{name,width,height,componentPropertyDefinitions}}]}`. Accepts exactly two plugin-created direct child Main COMPONENTs of a plugin-created FRAME, preserving both component IDs while combining them into a new Component Set. Supports Lower/H2-A, B, C, or Custom/name. All current values and parent child order are checked before renaming or combining. A repeat of the same key/spec reuses the existing set. An API failure returns `partialFailure` for inspection instead of blind retry. Use normal guarded patches for the new set's display layout if needed.

`inspect` additionally allows read-only `boundVariables` and `variantProperties`. An INSTANCE's `mainComponent` is already included automatically. Reading a variable reference alone does not confirm a live variable definition or library connection.

Document keys persist only in the named disposable test files: previous Komorebi Table and the new Mori/Sentinel/Paint test files. Other source files continue to use transient in-memory keys and receive no document pluginData write.

## 複数ファイルの接続（0.1.4-multifile-layout.1）

GET /statusはclients配列を返す。connected=trueの対象をfileNameとdocumentKeyで選ぶ。clientはliveな対象が1つの場合だけ返す互換フィールドで、複数ならnull。GET /status?documentKey=KEY、CLIのstatus --document KEYで対象を絞れる。POST /jobsのdocumentKeyは必須。別ファイルの接続や実行中ジョブは対象の接続・切断・再接続で中断されない。同じセッションIDを別文書へ転用することは拒否する。

GET /ui-lockはleaseまたはnullを返す。POST /ui-lockはaction=acquire/renew/release。acquireにはownerとtarget、renew/releaseにはownerとleaseIdが必要。ttlSecondsは30〜300、既定120。実行には従来どおりローカル接続キーが必要。同じ所有者・対象へのacquireは既存leaseを返し、他担当なら409。同じ所有者でも異なる対象への取得は409。renew/releaseは一致するleaseだけ扱い、期限切れのleaseで新しい担当のロックを解除することはできない。UIロックはFigma側で強制するものではなく、協調するCodexが画面操作前に取得する。
