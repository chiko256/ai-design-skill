#!/usr/bin/env python3
"""
generate-mockups.py

APIを使わず、セクションプロンプトファイルから画像生成タスクを作る。

このスクリプトは外部APIキーを使わない。
Codex上の画像生成へ渡すためのプロンプト一式を
<PROJECT_DIR>/_imagegen/<page>/ に保存し、期待する保存先を mockups/<page>/ に揃える。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageStat

REQUESTED_IMAGE_WIDTH = 2880
ALLOWED_TARGET_RATIOS = {
    "short": {"3:1", "5:2"},
    "standard": {"16:9", "3:2"},
    "tall": {"4:3", "1:1"},
}
ALLOWED_HEIGHT_TYPES = {*ALLOWED_TARGET_RATIOS, "custom"}
TARGET_RATIO_TOLERANCE = 0.01
EDGE_BAND_RATIO = 0.01
EDGE_BAND_MIN_PX = 4
EDGE_BAND_MAX_PX = 32
EDGE_BAND_MAX_STDDEV = 18.0
EDGE_BAND_MAX_MEAN_DELTA = 24.0
REQUIRED_PROMPT_META_KEYS = {
    "target_sections",
    "source_text_exact",
    "section_purpose",
    "background_zone",
    "display_text_exact",
    "expected_output_file",
}
OPTIONAL_PROMPT_META_KEYS = {
    "combine_sections_explicit", "target_ratio",
    "decoration_level", "special_direction",
}
LEGACY_PROMPT_META_KEYS = {"background", "top_background", "bottom_background", "height_type", "text_scale_profile"}
ALLOWED_PROMPT_META_KEYS = (
    REQUIRED_PROMPT_META_KEYS | OPTIONAL_PROMPT_META_KEYS | LEGACY_PROMPT_META_KEYS
)
ART_DIRECTION_ROLES = ("主役", "統合", "奥行き", "見せ場")
DEFAULT_CONTENT_SURFACE_POLICY = "code-friendly-standard"
ALLOWED_CONTENT_SURFACE_POLICIES = {
    "code-friendly-standard",
    "texture-asset-explicit",
}
ALLOWED_DECORATION_LEVELS = {
    "あしらい",
    "あしらい少し",
    "なし",
}
DECORATION_PROMPT_LINES = {
    "あしらい": "- あしらい: TOPの雰囲気に合うあしらいを、セクションの見せ場になる十分な存在感で使用する。",
    "あしらい少し": "- あしらい: TOPの雰囲気に合うあしらいを控えめに使用する。箇所数は固定しない。",
    "なし": "- あしらい: このセクションには背景のあしらいを入れない。内容を説明する人物・UI・イラストは使用してよい。",
}
TYPOGRAPHY_PROMPT_LINES = (
    "- 同じ役割の文字の大きさ・階層感をページ全体で揃える。数字・短い英語・飾り文字は内容上必要な時だけ強調してよい。",
    "- 通常のPC版Webサイトとして構成し、ポスターやプレゼン資料のような巨大文字にしない。",
    "- 余白を埋める目的で文字、行間、反復項目を拡大しない。",
)
STANDARD_CONTENT_SURFACE_PROHIBITION = (
    "カードやパネルに、紙・布・水彩の表面テクスチャ、破れた縁、"
    "要素ごとに異なる不規則な輪郭を使用しない。"
)
CONTENT_SURFACE_CONFLICT_PATTERNS = (
    r"紙片",
    r"紙面の重なり",
    r"紙(?:のような|風の?|質感|テクスチャ)",
    r"和紙",
    r"破れた縁",
    r"ちぎれた縁",
    r"布(?:のような|風の?|質感|テクスチャ)",
    r"水彩(?:風|のような|テクスチャ)?",
    r"不規則な輪郭",
)
IMPLEMENTATION_DEPENDENCY_MATERIAL_PATTERN = re.compile(
    r"(?:線|ツタ|つる|蔓|波線|曲線|マスク|切り抜き|装飾|合成|コラージュ)"
)
IMPLEMENTATION_DEPENDENCY_ACTION_PATTERN = re.compile(
    r"(?:つな(?:ぐ|げる)|結ぶ|連な(?:る|り)|横断|またぐ|枝分かれ|循環|一体(?:化)?|まとめる)"
)
IMPLEMENTATION_INDEPENDENCE_EXEMPTION_PATTERN = re.compile(
    r"(?:独立背景素材|独立画像素材|1要素内で完結|各要素内で完結|接続しない|位置に依存しない)"
)
NO_PHOTO_DIRECTION_PHRASES = (
    "写真なし",
    "実写なし",
    "写真禁止",
    "実写禁止",
    "写真を使わない",
    "写真は使わない",
    "写真を使用しない",
    "実写を使わない",
    "実写は使わない",
    "実写を使用しない",
    "イラストのみ",
    "イラストだけ",
)
SIZE_POLICY_KEYS = {"layout_viewport_px", "content_width_px"}
LEGACY_FONT_SIZE_KEYS = {"h2_japanese_px", "h2_japanese_max_px"}
ALLOWED_PAGE_COMMON_META_KEYS = SIZE_POLICY_KEYS | LEGACY_FONT_SIZE_KEYS | {
    "background_palette",
    "background_zones",
    "content_surface_policy",
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        fail(f"PNG画像として寸法を確認できません: {path}")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def find_requested_width_mismatches(paths: list[Path]) -> list[str]:
    """希望幅と異なるPNGを、原寸を変更せずに報告する。"""
    issues: list[str] = []
    for path in paths:
        width, height = png_dimensions(path)
        if width <= 0 or height <= 0:
            fail(f"画像寸法が不正です: {path} ({width}x{height})")
        if width != REQUESTED_IMAGE_WIDTH:
            issues.append(
                f"{path}: 希望幅 {REQUESTED_IMAGE_WIDTH}px / 実寸 {width}x{height}"
            )
    return issues


def parse_target_ratio(value: object, num: int | None = None) -> tuple[str, float]:
    """`横:縦` の目標比率を正規化し、数値比率を返す。"""
    label = f"プロンプト{num}: " if num is not None else ""
    ratio = str(value).strip()
    match = re.fullmatch(r"([1-9]\d*(?:\.\d+)?)\s*:\s*([1-9]\d*(?:\.\d+)?)", ratio)
    if not match:
        fail(f"{label}target_ratio は `横:縦` で指定してください: {ratio}")
    width = float(match.group(1))
    height = float(match.group(2))
    return f"{match.group(1)}:{match.group(2)}", width / height


def validate_height_meta(meta: dict, num: int) -> None:
    """比率は指定時だけ検証し、旧入力の高さタイプも受け取る。"""
    if "target_ratio" not in meta:
        if "height_type" in meta:
            fail(f"プロンプト{num}: height_type 単独の指定は使わず、高さ・比率未指定なら両方を省略してください")
        return
    target_ratio, _ = parse_target_ratio(meta["target_ratio"], num)
    meta["target_ratio"] = target_ratio
    if "height_type" not in meta:
        return
    height_type = str(meta.get("height_type", "")).strip().lower()
    if height_type not in ALLOWED_HEIGHT_TYPES:
        fail(
            f"プロンプト{num}: height_type は short / standard / tall / custom のいずれかにしてください: "
            f"{height_type or '未指定'}"
        )
    if height_type != "custom" and target_ratio not in ALLOWED_TARGET_RATIOS[height_type]:
        allowed = " / ".join(sorted(ALLOWED_TARGET_RATIOS[height_type]))
        fail(
            f"プロンプト{num}: height_type={height_type} の target_ratio は {allowed} のいずれかにしてください: "
            f"{target_ratio}"
        )
    meta["height_type"] = height_type
    meta["target_ratio"] = target_ratio


def find_target_ratio_mismatches(manifest: list[dict]) -> list[str]:
    """完成PNGとmanifestの目標比率の不一致を返す。"""
    issues: list[str] = []
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            fail(f"manifest.json: {index}件目がオブジェクトではありません")
        output = item.get("expected_output")
        if not output:
            fail(f"manifest.json: {index}件目に expected_output がありません")
        if "target_ratio" not in item:
            continue
        target_ratio, target_value = parse_target_ratio(item["target_ratio"], index)
        width, height = png_dimensions(Path(str(output)))
        actual_value = width / height
        if abs(actual_value - target_value) / target_value > TARGET_RATIO_TOLERANCE:
            issues.append(
                f"{Path(str(output)).name}: 目標 {target_ratio} / 実寸 {width}x{height}"
            )
    return issues


def normalize_text(value: str) -> str:
    """検証用に空白差分を吸収する。"""
    return re.sub(r"\s+", "", value or "")


def parse_yaml_meta(raw: str) -> dict:
    """工程06用の小さなYAMLメタブロックだけを読む。"""
    meta: dict[str, object] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not match:
            fail(f"yamlメタブロックを解析できません: {line}")
        key, value = match.group(1), match.group(2)
        if value in {"|-", "|", ">-", ">"}:
            block_lines: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block_lines.append(lines[i][2:] if lines[i].startswith("  ") else "")
                i += 1
            if value.startswith(">"):
                meta[key] = " ".join(line.strip() for line in block_lines if line.strip()).strip()
            else:
                meta[key] = "\n".join(block_lines).strip()
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
            i += 1
            continue
        if value == "":
            items: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  - ") or not lines[i].strip()):
                if lines[i].startswith("  - "):
                    items.append(lines[i][4:].strip())
                i += 1
            meta[key] = items
            continue
        meta[key] = value.strip().strip('"').strip("'")
        i += 1
    return meta


def extract_prompts(input_md: Path) -> list[dict]:
    """構造化 section-prompts.md から許可済みYAMLメタを抽出する。"""
    content = input_md.read_text(encoding="utf-8")
    pattern = (
        r"### プロンプト(\d+)：(.+?)\n+"
        r"```yaml\n([\s\S]+?)```"
        r"(?:\n+```text\n([\s\S]+?)```)?"
    )
    matches = re.findall(pattern, content)
    prompts = []
    for n, title, yaml_text, supplemental_prompt in matches:
        if (supplemental_prompt or "").strip():
            fail(
                f"プロンプト{n}: YAML後の自由記述textブロックは使用できません。"
                "画像生成へ渡す内容は許可済みYAMLメタだけにしてください"
            )
        prompts.append(
            {
                "num": int(n),
                "title": title.strip(),
                "meta": parse_yaml_meta(yaml_text),
            }
        )
    return sorted(prompts, key=lambda x: x["num"])


def validate_background_id(value: str, label: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
        fail(f"{label}は英数字・ハイフン・アンダースコアで指定してください: {value or '未指定'}")
    return value


def validate_content_surface_policy(value: object) -> str:
    policy = str(value or DEFAULT_CONTENT_SURFACE_POLICY).strip()
    if policy not in ALLOWED_CONTENT_SURFACE_POLICIES:
        allowed = " / ".join(sorted(ALLOWED_CONTENT_SURFACE_POLICIES))
        fail(
            "ページ共通計画: content_surface_policy は "
            f"{allowed} のいずれかにしてください: {policy or '未指定'}"
        )
    return policy


def extract_background_plan(input_md: Path) -> dict | None:
    """section-prompts.md のページ共通計画を読む。旧ファイルでは None を返す。"""
    content = input_md.read_text(encoding="utf-8")
    match = re.search(r"## (?:ページ共通計画|背景計画)\s*\n+```yaml\n([\s\S]+?)```", content)
    if not match:
        return None
    meta = parse_yaml_meta(match.group(1))
    unknown = sorted(set(meta) - ALLOWED_PAGE_COMMON_META_KEYS)
    if unknown:
        fail(
            "ページ共通計画: 許可されていないYAMLメタがあります: "
            + ", ".join(unknown)
        )
    palette_entries = meta.get("background_palette")
    zone_entries = meta.get("background_zones")
    if not isinstance(palette_entries, list) or not palette_entries:
        fail("背景計画: background_palette を1件以上指定してください")
    if not isinstance(zone_entries, list) or not zone_entries:
        fail("背景計画: background_zones を1件以上指定してください")

    palette: list[dict] = []
    palette_ids: set[str] = set()
    for entry in palette_entries:
        parts = [part.strip() for part in str(entry).split("|")]
        if len(parts) != 2 or not all(parts):
            fail(f"背景計画: background_palette は `色ID | 色指定` で書いてください: {entry}")
        palette_id = validate_background_id(parts[0], "背景色ID")
        if palette_id in palette_ids:
            fail(f"背景計画: 背景色IDが重複しています: {palette_id}")
        palette_ids.add(palette_id)
        palette.append({"id": palette_id, "value": parts[1]})

    zones: list[dict] = []
    zone_ids: set[str] = set()
    for entry in zone_entries:
        parts = [part.strip() for part in str(entry).split("|")]
        if len(parts) != 4 or not all(parts):
            fail(
                "背景計画: background_zones は "
                f"`ゾーンID | 色ID | 背景の扱い | mock-up番号` で書いてください: {entry}"
            )
        zone_id = validate_background_id(parts[0], "背景ゾーンID")
        palette_id = validate_background_id(parts[1], "背景色ID")
        if zone_id in zone_ids:
            fail(f"背景計画: 背景ゾーンIDが重複しています: {zone_id}")
        if palette_id not in palette_ids:
            fail(f"背景計画: {zone_id} が未定義の背景色IDを参照しています: {palette_id}")
        try:
            mockups = [int(value.strip()) for value in parts[3].split(",") if value.strip()]
        except ValueError:
            fail(f"背景計画: {zone_id} のmock-up番号はカンマ区切りの整数にしてください: {parts[3]}")
        if not mockups or any(num <= 0 for num in mockups):
            fail(f"背景計画: {zone_id} のmock-up番号が不正です: {parts[3]}")
        if mockups != sorted(set(mockups)):
            fail(f"背景計画: {zone_id} のmock-up番号は重複なしの昇順にしてください: {parts[3]}")
        if mockups != list(range(mockups[0], mockups[-1] + 1)):
            fail(f"背景計画: {zone_id} は連続するmock-upだけを含めてください: {parts[3]}")
        zone_ids.add(zone_id)
        zones.append(
            {
                "id": zone_id,
                "palette_id": palette_id,
                "treatment": parts[2],
                "mockups": mockups,
            }
        )
    zones.sort(key=lambda zone: zone["mockups"][0])
    return {
        "palette": palette,
        "zones": zones,
        "size_policy": validate_size_policy(meta),
        "content_surface_policy": validate_content_surface_policy(
            meta.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY)
        ),
    }


def validate_size_policy(meta: dict) -> dict | None:
    """幅の2項目を検証する。旧H2値は生成基準へ持ち込まない。"""
    present = SIZE_POLICY_KEYS & meta.keys()
    if not present:
        return None
    missing = SIZE_POLICY_KEYS - meta.keys()
    if missing:
        fail("サイズ基準: 不足項目: " + ", ".join(sorted(missing)))
    values = {}
    for key in sorted(SIZE_POLICY_KEYS):
        raw = str(meta[key]).strip()
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
            fail(f"サイズ基準: {key} は単位なしの有限な正数にしてください")
        value = float(raw)
        if not 0 < value <= 1440:
            fail(f"サイズ基準: {key} は0より大きく1440以下にしてください")
        values[key] = value
    if values["layout_viewport_px"] != 1440:
        fail("サイズ基準: PCの基準画面幅は1440pxにしてください")
    if values["content_width_px"] >= values["layout_viewport_px"]:
        fail("サイズ基準: コンテンツ内側幅は基準画面幅より小さくしてください")
    return values


def size_policy_prompt_lines(policy: dict | None) -> list[str]:
    if not policy:
        return []
    return [
        "- 基準画面幅は1440px。以下の内側幅はCSS相当であり、PNGの画素数とは区別する。出力幅Wの画像上では内側幅をW/1440倍で表現する。",
        f"- 主要コンテンツの内側幅は{policy['content_width_px']:g}px、中央配置。これはpaddingを含む外寸ではなく、本文・カード群を収める領域の最大幅。短文まで引き伸ばさない。",
        "- 背景と意図的な大きな写真は左右全幅に広げてよい。ただし同じ背景ゾーンに接する上下辺の保護は維持する。",
    ]


def verify_size_policy_handoff(project_dir: Path, page: str, policy: dict | None) -> None:
    """05の採用値が06への転記で消えたり変わったりしていないか確認する。"""
    plan = project_dir / "_src" / page / "section-plan.md"
    if not plan.exists():
        return
    content = plan.read_text(encoding="utf-8")
    heading = re.search(r"^## サイズ基準\s*$", content, re.MULTILINE)
    if not heading:
        return
    section = re.split(r"\n## ", content[heading.end():], maxsplit=1)[0]
    block = re.search(r"```yaml\n([\s\S]+?)```", section)
    if not block:
        fail("section-plan.md: サイズ基準のYAMLがありません")
    expected = validate_size_policy(parse_yaml_meta(block.group(1)))
    if expected is None or expected != policy:
        fail("サイズ基準: section-plan.mdとsection-prompts.mdの幅の2項目が一致しません")


def legacy_background_plan(prompts: list[dict]) -> dict:
    """旧background / 上下背景を、連続する背景ゾーンへ変換する。"""
    palette: list[dict] = []
    palette_by_value: dict[str, str] = {}
    zones: list[dict] = []
    current_zone: dict | None = None

    for item in prompts:
        meta = item["meta"]
        if "background" in meta:
            background = str(meta["background"]).strip()
        elif "top_background" in meta and "bottom_background" in meta:
            top = str(meta["top_background"]).strip()
            bottom = str(meta["bottom_background"]).strip()
            background = top if normalize_background(top) == normalize_background(bottom) else f"{top}から{bottom}へ切り替わる背景"
        else:
            fail(f"プロンプト{item['num']}: 背景計画または旧backgroundがありません")
        if not background:
            fail(f"プロンプト{item['num']}: 旧backgroundが空です")

        normalized = normalize_background(background)
        palette_id = palette_by_value.get(normalized)
        if not palette_id:
            palette_id = f"BG-{len(palette) + 1:02d}"
            palette_by_value[normalized] = palette_id
            palette.append({"id": palette_id, "value": background})

        if current_zone is None or current_zone["palette_id"] != palette_id:
            current_zone = {
                "id": f"Zone-{len(zones) + 1:02d}",
                "palette_id": palette_id,
                "treatment": "旧背景指定から変換した背景面",
                "mockups": [],
            }
            zones.append(current_zone)
        current_zone["mockups"].append(item["num"])
        meta["background_zone"] = current_zone["id"]

    return {
        "palette": palette,
        "zones": zones,
        "content_surface_policy": DEFAULT_CONTENT_SURFACE_POLICY,
    }


def validate_background_plan(background_plan: dict, prompts: list[dict]) -> None:
    zones = background_plan["zones"]
    zone_by_id = {zone["id"]: zone for zone in zones}
    assigned: dict[int, str] = {}
    for zone in zones:
        for num in zone["mockups"]:
            if num in assigned:
                fail(f"背景計画: mock-up {num} が複数ゾーンに含まれています: {assigned[num]} / {zone['id']}")
            assigned[num] = zone["id"]

    prompt_nums = {item["num"] for item in prompts}
    assigned_nums = set(assigned)
    if prompt_nums != assigned_nums:
        missing = sorted(prompt_nums - assigned_nums)
        extra = sorted(assigned_nums - prompt_nums)
        details = []
        if missing:
            details.append("割り当てなし=" + ",".join(map(str, missing)))
        if extra:
            details.append("存在しないmock-up=" + ",".join(map(str, extra)))
        fail("背景計画とmock-up番号が一致しません: " + " / ".join(details))

    for item in prompts:
        zone_id = str(item["meta"].get("background_zone", "")).strip()
        if not zone_id:
            fail(f"プロンプト{item['num']}: background_zone がありません")
        if zone_id not in zone_by_id:
            fail(f"プロンプト{item['num']}: background_zone=`{zone_id}` は背景ゾーン計画にありません")
        if assigned[item["num"]] != zone_id:
            fail(
                f"プロンプト{item['num']}: background_zone=`{zone_id}` と背景ゾーン計画の割り当て="
                f"`{assigned[item['num']]}` が一致しません"
            )
        item["meta"]["background_zone"] = zone_id


def validate_expected_output_file(filename: object, page: str, num: int) -> str:
    if not isinstance(filename, str) or not filename.strip():
        fail(f"プロンプト{num}: expected_output_file が空です")
    filename = filename.strip()
    if "/" in filename or "\\" in filename:
        fail(f"プロンプト{num}: expected_output_file はファイル名のみ指定してください: {filename}")
    if not filename.endswith(".png"):
        fail(f"プロンプト{num}: expected_output_file は .png 必須です: {filename}")
    if not filename.startswith(f"{page}_{num:02d}_"):
        fail(f"プロンプト{num}: expected_output_file は {page}_{num:02d}_ で始めてください: {filename}")
    return filename


def display_text_values(display_text: str) -> list[str]:
    """`役割: 表示文字` から、画像へ描く値だけを取り出す。"""
    values: list[str] = []
    for raw_line in display_text.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip()
        if not line:
            continue
        if ":" in line or "：" in line:
            parts = re.split(r"[:：]", line, maxsplit=1)
            line = parts[1].strip()
        line = line.strip().strip("`").strip('"').strip("'").strip()
        if line:
            values.append(line)
    return values


def format_display_text_for_prompt(display_text: str) -> str:
    """表示値を変えず、反復項目の役割ラベルだけを個別prompt向けに明確化する。"""
    counters: dict[str, int] = {}
    output: list[str] = []
    paired_roles = {"項目", "機能", "導線", "カード"}
    numbered_roles = {"ボタン", "手順", "紹介", "お知らせ"}

    for raw_line in display_text.splitlines():
        line = raw_line.strip()
        if not line or (":" not in line and "：" not in line):
            if line:
                output.append(line)
            continue
        role, value = re.split(r"[:：]", line, maxsplit=1)
        role = role.strip()
        value = value.strip()
        role_key = role.lower()

        if role_key in {"q", "質問"}:
            counters["qa_question"] = counters.get("qa_question", 0) + 1
            output.append(f"Q{counters['qa_question']}: {value}")
            continue
        if role_key in {"a", "回答"}:
            counters["qa_answer"] = counters.get("qa_answer", 0) + 1
            output.append(f"A{counters['qa_answer']}: {value}")
            continue

        if role in paired_roles:
            counters[role] = counters.get(role, 0) + 1
            index = counters[role]
            parts = re.split(r"\s+[—–]\s+", value, maxsplit=1)
            if len(parts) == 1:
                parts = re.split(r"[:：]", value, maxsplit=1)
            if len(parts) == 2 and all(part.strip() for part in parts):
                output.append(f"{role}{index}見出し: {parts[0].strip()}")
                output.append(f"{role}{index}本文: {parts[1].strip()}")
            else:
                output.append(f"{role}{index}: {value}")
            continue

        if role in numbered_roles:
            counters[role] = counters.get(role, 0) + 1
            index = counters[role]
            if role == "お知らせ":
                match = re.fullmatch(r"(\d{4}\.\d{2}\.\d{2})\s+(.+)", value)
                if match:
                    output.append(f"お知らせ{index}日付: {match.group(1)}")
                    output.append(f"お知らせ{index}本文: {match.group(2)}")
                    continue
            output.append(f"{role}{index}: {value}")
            continue

        output.append(f"{role}: {value}")

    return "\n".join(output)


def forbids_icons(special_direction: str) -> bool:
    normalized = normalize_text(special_direction)
    return any(
        phrase in normalized
        for phrase in ("アイコンを使わない", "アイコンは使わない", "アイコンなし")
    )


def forbids_photo(special_direction: str) -> bool:
    """簡易アートディレクションに明示的な写真禁止があるか判定する。"""
    normalized = normalize_text(special_direction)
    return any(phrase in normalized for phrase in NO_PHOTO_DIRECTION_PHRASES)


def validate_special_direction(value: object, num: int) -> str:
    """任意の見せ方を検証し、旧形式の項目も不足補完せず受け取る。"""
    if not isinstance(value, str) or not value.strip():
        fail(f"プロンプト{num}: special_direction が空です")

    parsed: dict[str, str] = {}
    allowed_roles = {"見せ方", *ART_DIRECTION_ROLES, "特別な演出"}
    for raw_line in value.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip()
        if not line:
            continue
        match = re.fullmatch(r"([^:：]+)\s*[:：]\s*(.+)", line)
        if not match:
            fail(
                f"プロンプト{num}: special_direction は "
                "`見せ方`（旧形式は `主役 / 統合 / 奥行き / 見せ場`）と任意の `特別な演出` で記録してください: "
                f"{line}"
            )
        role, direction = match.group(1).strip(), match.group(2).strip()
        if role not in allowed_roles:
            fail(f"プロンプト{num}: special_direction に未許可の項目があります: {role}")
        if role in parsed:
            fail(f"プロンプト{num}: special_direction の項目が重複しています: {role}")
        if re.search(r"```|`?(?:source_text_exact|target_sections|expected_output_file|section_purpose)`?", direction):
            fail(f"プロンプト{num}: special_direction の `{role}` に管理メタを入れないでください")
        if re.search(r"\b\d+(?:\.\d+)?\s*px\b|\b(?:x|y)\s*=|\d+\s*カラム|カード幅|余白量|左右比率", direction, re.IGNORECASE):
            fail(f"プロンプト{num}: special_direction の `{role}` に詳細レイアウト指定を入れないでください")
        parsed[role] = direction

    normalized = [f"{role}: {parsed[role]}" for role in ("見せ方", *ART_DIRECTION_ROLES) if role in parsed]
    if "特別な演出" in parsed and normalize_text(parsed["特別な演出"]) != "なし":
        normalized.append(f"特別な演出: {parsed['特別な演出']}")
    return "\n".join(normalized)


def validate_content_surface_compatibility(
    special_direction: str,
    policy: str,
    num: int,
) -> None:
    """簡易アートディレクションとページ共通の実装方針の矛盾を止める。"""
    if policy != "code-friendly-standard":
        return
    normalized = normalize_text(special_direction)
    conflicts = [
        pattern
        for pattern in CONTENT_SURFACE_CONFLICT_PATTERNS
        if re.search(pattern, normalized)
    ]
    if conflicts:
        fail(
            f"プロンプト{num}: special_direction が content_surface_policy=code-friendly-standard "
            "と矛盾しています。紙・布・水彩の表面テクスチャ、破れた縁、不規則な輪郭を除くか、"
            "ユーザーの明示指定がある場合だけ texture-asset-explicit を選んでください"
        )


def validate_implementation_independence(special_direction: str, num: int) -> None:
    """複数要素の描き直しを前提にする視覚統合を生成前に止める。"""
    normalized = normalize_text(special_direction)
    has_material = IMPLEMENTATION_DEPENDENCY_MATERIAL_PATTERN.search(normalized)
    has_action = IMPLEMENTATION_DEPENDENCY_ACTION_PATTERN.search(normalized)
    has_exemption = IMPLEMENTATION_INDEPENDENCE_EXEMPTION_PATTERN.search(normalized)
    if has_material and has_action and not has_exemption:
        fail(
            f"プロンプト{num}: special_direction が実装独立性と矛盾しています。"
            "線・マスク・切り抜き・装飾・合成・コラージュで複数要素を一体化せず、"
            "共通色・書体・文字階層・輪郭・アイコン・余白・反復規則・背景面で関係づけるか、"
            "1要素内で完結する装飾または位置に依存しない独立素材として明示してください"
        )


def normalize_background(value: object) -> str:
    return normalize_text(str(value).replace("`", "")).lower()


def validate_prompt_meta(
    prompts: list[dict],
    project_dir: Path,
    page: str,
    content_surface_policy: str,
) -> None:
    texts_path = project_dir / "texts.md"
    if not texts_path.exists():
        fail(f"{texts_path} が見つかりませんでした")
    texts_content = normalize_text(texts_path.read_text(encoding="utf-8"))
    expected_files: set[str] = set()
    for item in prompts:
        num = item["num"]
        meta = item["meta"]
        unknown = sorted(set(meta) - ALLOWED_PROMPT_META_KEYS)
        if unknown:
            fail(
                f"プロンプト{num}: 許可されていないYAMLメタがあります: {', '.join(unknown)}。"
                "画像生成へ渡す項目を増やさず、許可済みメタだけを使ってください"
            )
        missing = sorted(REQUIRED_PROMPT_META_KEYS - set(meta))
        if missing:
            fail(f"プロンプト{num}: 必須メタが不足しています: {', '.join(missing)}")
        if not isinstance(meta["target_sections"], list) or not meta["target_sections"]:
            fail(f"プロンプト{num}: target_sections は1件以上のリストにしてください")
        meta["target_sections"] = [str(section).strip() for section in meta["target_sections"]]
        if not all(meta["target_sections"]):
            fail(f"プロンプト{num}: target_sections に空項目があります")
        combine_explicit = str(meta.get("combine_sections_explicit", "no")).strip().lower() in {"yes", "true", "1"}
        if len(meta["target_sections"]) == 2 and not combine_explicit:
            fail(f"プロンプト{num}: 通常は1セクション1画像です。2セクションを1画像にする場合は、ユーザーの明示希望がある時だけ combine_sections_explicit: yes を指定してください")
        if len(meta["target_sections"]) > 2:
            fail(f"プロンプト{num}: 3セクション以上を1画像にできません。工程5へ戻してmock-up単位を分けてください")
        meta["combine_sections_explicit"] = "yes" if combine_explicit else "no"
        source_text = meta["source_text_exact"]
        if not isinstance(source_text, str) or not source_text.strip():
            fail(f"プロンプト{num}: source_text_exact が空です")
        if normalize_text(source_text) not in texts_content:
            fail(f"プロンプト{num}: source_text_exact が texts.md に見つかりません")
        for key in ["section_purpose", "background_zone", "display_text_exact"]:
            value = meta[key]
            if not isinstance(value, str) or not value.strip():
                fail(f"プロンプト{num}: {key} が空です")
            meta[key] = value.strip()
        visible_values = display_text_values(str(meta["display_text_exact"]))
        if not visible_values:
            fail(f"プロンプト{num}: display_text_exact に表示文字がありません")
        normalized_source = normalize_text(str(source_text))
        for visible_value in visible_values:
            if normalize_text(visible_value) not in normalized_source:
                fail(
                    f"プロンプト{num}: display_text_exact の値が source_text_exact に見つかりません: {visible_value}"
                )
        meta.pop("text_scale_profile", None)  # 旧プロファイルは受け取るだけで再適用しない。
        if "decoration_level" in meta:
            decoration_level = str(meta["decoration_level"]).strip()
            if decoration_level not in ALLOWED_DECORATION_LEVELS:
                allowed = " / ".join(sorted(ALLOWED_DECORATION_LEVELS))
                fail(
                    f"プロンプト{num}: decoration_level は {allowed} のいずれかにしてください: "
                    f"{decoration_level or '空欄'}"
                )
            meta["decoration_level"] = decoration_level
        if "special_direction" in meta:
            meta["special_direction"] = validate_special_direction(meta["special_direction"], num)
            validate_content_surface_compatibility(
                str(meta["special_direction"]), content_surface_policy, num
            )
            validate_implementation_independence(str(meta["special_direction"]), num)
        validate_height_meta(meta, num)
        expected_output_file = validate_expected_output_file(meta["expected_output_file"], page, num)
        if expected_output_file in expected_files:
            fail(f"プロンプト{num}: expected_output_file が重複しています: {expected_output_file}")
        expected_files.add(expected_output_file)
        meta["expected_output_file"] = expected_output_file

def section_slug(title: str) -> str:
    """プロンプトタイトルをファイル名用スラッグにする。"""
    slug = re.sub(r"[^\w぀-鿿]", "-", title)
    return slug.strip("-")[:40] or "section"


def clean_markdown_value(value: str) -> str:
    """Markdownの箇条書きやバッククォートを外して値だけにする。"""
    value = value.strip()
    value = re.sub(r"^\s*[-*]\s*", "", value)
    value = value.strip().strip("`").strip()
    return value


def extract_selected_fv_image(project_dir: Path) -> Path | None:
    """_fv/selected-option.md からCanva/Figma転送用FV画像パスを優先取得する。"""
    selected_path = project_dir / "_fv" / "selected-option.md"
    if not selected_path.exists():
        fail(f"{selected_path} が見つかりませんでした\n工程04で採用FV画像パスを _fv/selected-option.md に保存してから再実行してください。")

    content = selected_path.read_text(encoding="utf-8")
    match = re.search(r"Canva/Figma転送用FV画像パス\s*[:：]\s*(.+)", content)
    if not match:
        match = re.search(r"採用FV画像パス\s*[:：]\s*(.+)", content)
    if not match:
        fail(f"{selected_path} に採用FV画像パスが見つかりませんでした\n`Canva/Figma転送用FV画像パス: <path>` または `採用FV画像パス: <path>` の形式で保存してから再実行してください。")

    raw_path = clean_markdown_value(match.group(1))
    if not raw_path:
        fail(f"{selected_path} の採用FV画像パスが空です")

    fv_image = Path(raw_path)
    if not fv_image.is_absolute():
        fv_image = project_dir / fv_image

    if not fv_image.exists():
        fail(f"採用FV画像が見つかりませんでした: {fv_image}\n採用FV画像パスが正しいか確認してから再実行してください。")

    return fv_image


def extract_page_flow(project_dir: Path, page: str) -> str | None:
    """_src/<page>/section-plan.md からページ全体の流れを取得する。"""
    section_plan = project_dir / "_src" / page / "section-plan.md"
    if not section_plan.exists():
        return None

    content = section_plan.read_text(encoding="utf-8")
    match = re.search(r"## ページ全体の流れ\s*\n+([\s\S]+?)(?:\n## |\Z)", content)
    if not match:
        return None

    flow = match.group(1).strip()
    return flow or None


def unique_paths(paths: list[Path]) -> list[Path]:
    """順序を保ったまま重複パスを取り除く。"""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def extract_revision_prompt(content: str) -> str | None:
    """revision-request.md から修正プロンプト本文を抽出する。"""
    match = re.search(r"## 修正プロンプト\s*\n+([\s\S]+?)(?:\n## |\Z)", content)
    if not match:
        return None
    prompt = match.group(1).strip()
    return prompt or None


def extract_revision_targets(content: str, project_dir: Path) -> list[Path]:
    """revision-request.md から対象mock-up画像パスを抽出する。"""
    scope_match = re.search(r"## 修正対象スコープ\s*\n+([\s\S]+?)(?:\n## |\Z)", content)
    if not scope_match:
        fail("revision-request.md に `## 修正対象スコープ` が見つかりませんでした")
    search_area = scope_match.group(1)
    raw_paths = re.findall(r"`([^`]+\.png)`", search_area)

    targets: list[Path] = []
    for raw_path in raw_paths:
        target = Path(clean_markdown_value(raw_path))
        if not target.is_absolute():
            target = project_dir / target
        targets.append(target)
    return unique_paths(targets)


def write_revision_tasks(
    project_dir: str,
    page: str = "top",
    revision_file: str | None = None,
    quality: str = "high",
    open_output: bool = False,
) -> None:
    """revision-request.md から、対象mock-upだけの再生成タスクを作る。"""
    if quality != "high":
        fail("工程06の正式mock-up修正は --quality high だけを使用してください")
    base = Path(project_dir)
    revision_md = Path(revision_file) if revision_file else base / "_src" / page / "revision-request.md"
    out_dir = base / "mockups" / page
    task_dir = base / "_imagegen" / page
    backup_dir = out_dir / "revision-backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    if not revision_md.exists():
        fail(f"{revision_md} が見つかりませんでした\n工程06の承認・修正ルーティングで _src/top/revision-request.md を保存してから再実行してください。")

    content = revision_md.read_text(encoding="utf-8")
    revision_prompt = extract_revision_prompt(content)
    if not revision_prompt:
        fail(f"{revision_md} に `## 修正プロンプト` が見つかりませんでした")

    targets = extract_revision_targets(content, base)
    if not targets:
        fail(f"{revision_md} の `## 修正対象スコープ` に対象mock-up画像パスが見つかりませんでした")

    missing = [target for target in targets if not target.exists()]
    if missing:
        fail("対象mock-up画像が見つかりませんでした\n" + "\n".join(f"- {target}" for target in missing))

    backup_dir.mkdir(parents=True, exist_ok=True)

    source_prompts = base / "_src" / page / "section-prompts.md"
    revision_size_policy = None
    if source_prompts.exists():
        source_plan = extract_background_plan(source_prompts)
        if source_plan:
            revision_size_policy = source_plan.get("size_policy")
    verify_size_policy_handoff(base, page, revision_size_policy)

    manifest: list[dict] = []
    queue_lines = [
        f"# 修正画像生成タスク — {page}",
        "",
        "工程06の revision-request.md をもとに、対象mock-upだけを再生成するためのキューです。",
        "対象外mock-upは再生成せず、既存画像を維持してください。",
        "",
        f"- revision_request: `{revision_md}`",
        f"- quality_hint: `{quality}`",
        "",
    ]

    for index, target in enumerate(targets, start=1):
        slug = section_slug(target.stem)
        task_file = task_dir / f"revision-task-{index:02d}-{slug}.md"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_file = backup_dir / f"{target.stem}_before_{timestamp}{target.suffix}"
        counter = 2
        while backup_file.exists():
            backup_file = backup_dir / f"{target.stem}_before_{timestamp}_{counter}{target.suffix}"
            counter += 1
        shutil.copy2(target, backup_file)

        final_prompt = "\n\n".join(
            [
                f"添付された対象mock-up画像 `{target}` をベースにしてください。",
                "対象外のセクションや画像は変更しないでください。",
                "修正後の画像は expected_output のパスへ保存し、同じ位置のmock-upとして差し替えてください。",
                f"修正後のPNGは可能なら横幅{REQUESTED_IMAGE_WIDTH}pxにしてください。Imagegenが異なる幅を返した場合は加工せず原寸で保存してください。レイアウトは1440px幅のWebセクションとして扱い、横に広い別レイアウトにはしないでください。",
                revision_prompt,
                "\n".join([*TYPOGRAPHY_PROMPT_LINES, *size_policy_prompt_lines(revision_size_policy)]),
            ]
        )

        task_file.write_text(
            "\n".join(
                [
                    f"# 修正タスク{index}：{target.stem}",
                    "",
                    f"- expected_output: `{target}`",
                    f"- reference_image: `{target}`",
                    f"- backup_image: `{backup_file}`",
                    "- style_ref: なし",
                    "- style_ref_attachment_required: no",
                    "- style_ref_attachment_status: not_required",
                    f"- requested_image_width: {REQUESTED_IMAGE_WIDTH}px",
                    "- revision_request: `" + str(revision_md) + "`",
                    "",
                    "## 生成プロンプト",
                    "",
                    "```text",
                    final_prompt,
                    "```",
                    "",
                    "## 保存後チェック",
                    "",
                    "- `expected_output` の画像だけが更新されているか確認する。",
                    "- 対象外mock-up画像を再生成していないか確認する。",
                    f"- 横幅{REQUESTED_IMAGE_WIDTH}pxは希望値として確認する。異なる場合は補正せず、原寸を記録して使用する。",
                    "- 保存後、`mockups/top/full_preview.html` をmanifest順で更新する。",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        manifest.append(
            {
                "num": index,
                "task_file": str(task_file),
                "expected_output": str(target),
                "reference_image": str(target),
                "backup_image": str(backup_file),
                "revision_request": str(revision_md),
            }
        )
        queue_lines.extend(
            [
                f"## {index}. {target.name}",
                "",
                f"- task_file: `{task_file}`",
                f"- expected_output: `{target}`",
                f"- reference_image: `{target}`",
                f"- backup_image: `{backup_file}`",
                "",
            ]
        )

    (task_dir / "revision-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (task_dir / "revision-queue.md").write_text("\n".join(queue_lines), encoding="utf-8")

    print("✅ 修正画像生成タスクを作成しました（API未使用）")
    print(f"   修正キュー: {task_dir / 'revision-queue.md'}")
    print(f"   対象画像: {len(targets)}件")
    print("   次は Codex上の画像生成に各 revision-task-*.md のプロンプトを渡してください。")

    print("   修正画像を保存後、--preview-only で通常 manifest 順の full_preview.html を更新してください。")
    if open_output:
        subprocess.run(["open", str(task_dir)], check=False)


def background_zone_details(background_plan: dict, zone_id: str) -> tuple[str, str, str]:
    zone = next((item for item in background_plan["zones"] if item["id"] == zone_id), None)
    if zone is None:
        fail(f"背景ゾーン `{zone_id}` が背景計画にありません")
    palette = next(
        (item for item in background_plan["palette"] if item["id"] == zone["palette_id"]),
        None,
    )
    if palette is None:
        fail(f"背景色 `{zone['palette_id']}` が背景計画にありません")
    return str(zone["palette_id"]), str(palette["value"]), str(zone["treatment"])


def protected_edge_label(sections: list[dict], index: int) -> str | None:
    """背景ゾーンの隣接関係から保護する接続辺を返す。"""
    zone_id = str(sections[index]["background_zone"])
    same_top = index > 0 and str(sections[index - 1]["background_zone"]) == zone_id
    same_bottom = (
        index + 1 < len(sections)
        and str(sections[index + 1]["background_zone"]) == zone_id
    )
    if not same_top and not same_bottom:
        return None
    return "上端と下端" if same_top and same_bottom else "上端" if same_top else "下端"


def same_zone_edge_prompt_lines(sections: list[dict], index: int) -> list[str]:
    """同じ背景ゾーンへ接する辺の、検証可能な完了条件を返す。"""
    edge = protected_edge_label(sections, index)
    if edge is None:
        return []
    return [
        f"- 接続する{edge}の全幅は背景ゾーンの背景面だけにし、図形、線、写真、カード、影、光、グラデーション、マスクを接触させない。",
        f"- FVの外周に接する装飾を接続する{edge}へコピーしない。",
    ]


def decoration_prompt_line(decoration_level: str, protected_edge: str | None) -> str:
    """あしらい量と接続辺保護を競合しない1文へまとめる。"""
    base = DECORATION_PROMPT_LINES[decoration_level]
    if protected_edge is None:
        return base
    return (
        f"{base} 接続する{protected_edge}から離し、"
        "あしらいはすべて画像内で完結させる。"
    )


def build_individual_prompt(
    selected_fv_image: Path,
    background_plan: dict,
    item: dict,
    same_zone_edge_lines: list[str] | None = None,
) -> str:
    palette_id, palette_value, zone_treatment = background_zone_details(
        background_plan, str(item["background_zone"])
    )
    content_surface_policy = validate_content_surface_policy(
        background_plan.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY)
    )
    direction = str(item.get("special_direction", "")).strip()
    decoration_level = str(item["decoration_level"]).strip() if "decoration_level" in item else None
    if decoration_level is not None and decoration_level not in ALLOWED_DECORATION_LEVELS:
        fail(f"個別prompt: 未対応の decoration_level です: {decoration_level}")
    protected_edge = None
    if same_zone_edge_lines:
        match = re.search(r"接続する(上端と下端|上端|下端)", same_zone_edge_lines[0])
        protected_edge = match.group(1) if match else None
    lines = [
        "$imagegen",
        "",
        "## 生成対象",
        "",
        f"- 目的: {item['section_purpose']}",
        f"- 保存先: `{item['expected_output']}`",
        (f"- 完成フレーム: 目標比率 `{item['target_ratio']}`" if item.get("target_ratio") else "- 完成フレーム: 文字の一貫性とコンテンツ幅を保ち、掲載内容に合う自然な高さで構成する。高さ・比率の数値は指定しない。"),
        f"- 背景ゾーン: `{item['background_zone']}` / `{palette_id}` / {palette_value} / {zone_treatment}",
        f"- 出力: 可能なら横幅{REQUESTED_IMAGE_WIDTH}pxの高精細PNG。異なる幅で生成された場合は原寸を維持する",
        "",
        "## 文字の扱い",
        "",
        *TYPOGRAPHY_PROMPT_LINES,
        "",
        "## 共通デザイン",
        "",
        *size_policy_prompt_lines(background_plan.get("size_policy")),
        f"- `--image {selected_fv_image}` で添付された採用FVをスタイル参照として使う。",
        "- 採用FVの配色、書体の系統と太さ、角丸、線、アイコン、イラスト、ボタン、UIの雰囲気を継承する。",
        "- 写真とイラストの構成も採用FVに合わせる。採用FVが写真とイラストの組み合わせなら、FV以下もページ全体として両方を使い、内容に合うセクションへ配分する。採用FVがイラストのみなら、明示指定がない限り写真を追加しない。",
        "- 写真を使う場合は、採用FVから色調、光、撮影品質だけを継承する。",
        "- FVのレイアウト、FVのH1サイズ、FV固有の装飾モチーフを機械的にコピーしない。",
        ("- 共通サイズ基準の内側幅を守り、文字、CTA、人物の顔を画面端で切らない。" if background_plan.get("size_policy") else "- 文字、CTA、人物の顔などの主要部分を画面端で切らず、左右に自然な余白を残す。固定pxでは判定しない。"),
        "- 背景色は画面端まで広げてよい。背景色以外は、同じ背景ゾーンと接する辺まで広げない。",
        "- 同じ背景ゾーンの途中へ、区切り線、色差、グラデーションの切り替わりを追加しない。",
        "",
        "## 実装可能性",
        "",
        "- 背景、写真・イラスト、文字、操作要素、装飾、反復項目の責任範囲を判別できる形にする。",
        "- 1要素の移動、サイズ変更、文言変更、画像差し替えで、別要素を描き直す必要がある構造にしない。",
        "- 反復要素は1つの共通ルールで再現し、個別に移動・追加・削除・並び替えできる形にする。",
        "- 装飾は1要素内で完結させるか、コンテンツ位置に依存しない独立背景素材にする。",
        "- PCからスマートフォンへの変更は、並び替え、積み重ね、縮小、独立装飾の非表示で説明できる形にし、線、マスク、切り抜き、装飾、合成画像の描き直しを必要としない。",
        "- 複雑な写真やイラストは、文字や操作要素と分離できる明確な境界があれば、1つの独立画像素材として残してよい。",
        "- 実装の都合だけで、すべてを均等カードや汎用グリッドにしない。",
        "",
        "## 見せ方",
        "",
        "- 個別指定がない見せ方・あしらい・アイコンは、内容と採用FVの雰囲気に応じて判断する。",
        *([decoration_prompt_line(decoration_level, protected_edge)] if decoration_level is not None else []),
    ]
    lines.extend(f"- {line.strip()}" for line in direction.splitlines() if line.strip())
    if item["combine_sections_explicit"] == "yes":
        lines.append("- 指定された2セクションを、この画像だけは1枚にまとめる。")
    lines.extend(
        [
            "",
            "## 表示文字",
            "",
            "コロンより左側は構造を伝える管理ラベルであり、画像には描かない。コロンより右側の値だけを表示する。見出しと本文など、同じ番号または同じ役割の組を対応づけて扱う。",
            "",
            "```text",
            format_display_text_for_prompt(str(item["display_text_exact"])),
            "```",
            "",
            "## 禁止事項",
            "",
            "- ヘッダー、ロゴ、ナビ、FV、mock-up番号、Section番号、Markdown記号、管理ラベルを描かない。",
            "- 指定されていない説明文、コピー、固有名詞、数値を追加しない。",
            "- 添付FV内の写真そのもの、切り抜き、ほぼ同一の再現を使用しない。",
            "- FV写真と同一のポーズ、背景、構図を使用しない。",
            "- HTML/CSSレンダリング、ブラウザ撮影、Canvas、SVG、手作業の画像合成を完成PNGの生成元にしない。",
            "- 文字の正確さを理由にAI画像生成以外へ切り替えない。",
        ]
    )
    if content_surface_policy == DEFAULT_CONTENT_SURFACE_POLICY:
        lines.append(f"- {STANDARD_CONTENT_SURFACE_PROHIBITION}")
    if forbids_photo(direction):
        lines.append(
            "- 簡易アートディレクションに写真なしが明示されているため、このmock-upでは写真を配置せず、添付FV内の写真も使用しない。"
        )
    if forbids_icons(direction):
        lines.append("- このmock-upではアイコンを描かない。文字、罫線、余白で情報を整理する。")
    lines.extend(
        [
            "",
            "## 完了条件",
            "",
            f"- 完成PNGは可能なら横幅{REQUESTED_IMAGE_WIDTH}pxで指定保存先へ保存する。異なる幅で生成された場合は加工せず原寸で保存する。",
            "- 文字、写真、あしらい、背景変化を完成フレーム内へ収め、主要要素を切って比率を合わせない。",
            *(same_zone_edge_lines or []),
            ("- この個別プロンプトは1回生成する。目標比率と異なる場合だけ1回再生成し、再生成後も異なる場合は再生成結果と実寸を記録して次へ進む。" if item.get("target_ratio") else "- この個別プロンプトは1回生成する。比率を理由に再生成せず、実寸を記録する。"),
            "- 内容が窮屈でないか、不要な空きが多くないかを確認し、問題があれば自動再生成せず報告する。",
            "- 生成または保存に失敗した場合は、別方式や同じプロンプトの再実行へ切り替えず停止する。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_batch_prompt(
    selected_fv_image: Path,
    background_plan: dict,
    manifest: list[dict],
) -> str:
    lines = [
        "$imagegen",
        "",
        "生成方式（最優先・必須）:",
        "- `prompts/individual/` の独立プロンプトを番号順に読み、各ファイルを1回の `$imagegen` 呼び出しへ1本ずつ渡す。",
        f"- {len(manifest)}枚を1回の画像生成へまとめず、1画像につき1プロンプトで生成する。",
        "- 各完成PNGは、必ず `$imagegen` によるAI画像生成で作る。添付FVは全画像のスタイル参照として使う。",
        "- HTML/CSSレンダリング、Playwrightなどのブラウザ撮影、Canvas、SVG、手作業の画像合成を完成mock-upの生成元にしない。",
        "- 日本語文字の正確さを理由に、HTML/CSSやブラウザ撮影へ切り替えない。軽微な文字差は許容する。",
        ("- 各対象は1回生成する。目標比率がある対象に限り、目標比率と異なる場合だけ1回再生成し、再生成後も異なる場合は再生成結果と実寸を記録して次へ進む。比率未指定の対象は比率を理由に再生成しない。" if any(item.get("target_ratio") for item in manifest) else "- 各対象は1回生成する。比率を理由に再生成しない。"),
        "- 生成・保存の失敗時は生成済み画像を保持して停止する。",
        "",
        "目的:",
        "添付FVから続く1つのWebサイトとして、個別プロンプトからFV下のmock-upを1枚ずつ生成する。",
        "個別プロンプトを画像生成内容の正本とし、このbatchは実行順とページ全体の連続性だけを管理する。",
        "",
        "入力画像:",
        f"この子Codex実行には `--image {selected_fv_image}` で採用FV画像が添付されている。",
        "",
        "ページ全体で守る3つの共通ルール:",
        "1. 背景ゾーンの連続性: 同じ背景ゾーンでは背景色だけを連続させ、セクション境界にあしらいを配置しない。",
        "2. 文字の一貫性: 同じ役割の文字の大きさ・階層感を全mock-upで揃える。巨大文字や余白を埋めるための拡大を避ける。",
        "3. FVの雰囲気継承: 添付FVの配色、書体の系統と太さ、角丸、線、アイコン、イラスト、ボタンに加え、写真のみ・イラストのみ・写真とイラストの組み合わせという構成も受け継ぐ。写真は色調、光、撮影品質だけを継承し、FV写真そのもの、切り抜き、ほぼ同一の再現、同一のポーズ・背景・構図は使用しない。FVのレイアウト、H1サイズ、装飾モチーフを機械的にコピーしない。",
        "",
    ]
    lines.extend(["背景計画（ページ共通）:", "背景パレット:"])
    for palette in background_plan["palette"]:
        lines.append(f"- {palette['id']}: {palette['value']}")
    lines.append("背景ゾーン:")
    for zone in background_plan["zones"]:
        mockups = ", ".join(str(num) for num in zone["mockups"])
        lines.append(
            f"- {zone['id']}: {zone['palette_id']} / {zone['treatment']} / 対象mock-up {mockups}"
        )
    lines.extend(
        [
            "背景ゾーンルール:",
            "- 同じゾーンの途中に、区切り線、色差、グラデーションの切り替わりを作らない。",
            "- 背景を変更するのは、背景ゾーンが変わる境界だけにする。",
            "- カード、UI、写真枠、CTAパネルの色は、ページ背景とは区別する。",
            "- セクションが変わることだけを理由に背景色を変更しない。",
            "",
        ]
    )
    lines.append("個別プロンプト実行順:")
    for item in manifest:
        lines.extend(
            [
                "",
                f"mock-up {int(item['num'])}",
                f"個別プロンプト: `{item['individual_prompt_file']}`",
                f"保存先: `{item['expected_output']}`",
                *([f"完成フレーム: 目標比率 `{item['target_ratio']}`"] if item.get("target_ratio") else []),
                f"背景ゾーン: {item['background_zone']}",
            ]
        )

    lines.extend(
        [
            "",
            "完了条件:",
            f"- {len(manifest)}本の個別プロンプトを番号順に1本ずつ実行し、{len(manifest)}枚すべてを指定の保存先へ保存する。",
            f"- 各PNGは横幅{REQUESTED_IMAGE_WIDTH}pxを希望値として確認する。異なる場合は補正・再生成せず、原寸を記録して使用する。",
            ("- 目標比率があるPNGだけ比率を確認し、再生成の扱いは上記の実行条件に従う。" if any(item.get("target_ratio") for item in manifest) else "- 各PNGの実寸を記録する。比率の合否判定はしない。"),
            "- 画像は原則1セクション1枚に分ける。2セクションを1枚にするのは、上で明示したmock-upだけにする。",
            "- 内容の窮屈さ・不要な空き、文字の一貫性・過度な拡大を目視し、問題があれば自動再生成せず報告する。それ以外の流体表現、文言、構成、セクション間統一の品質検査は行わない。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_prompt_spec(
    task_dir: Path,
    page: str,
    mode: str,
    input_md: Path,
    out_dir: Path,
    selected_fv_image: Path,
    selected_fv_sha256: str,
    page_flow: str | None,
    quality: str,
    batch_prompt_file: Path,
    individual_prompt_dir: Path,
    batch_child_run_log: Path,
    batch_image_input_argument: str,
    background_plan: dict,
    sections: list[dict],
) -> Path:
    """画像生成へ渡す前の構造化prompt正本を保存する。"""
    spec = {
        "schema_version": 6,
        "page": page,
        "mode": mode,
        "source": str(input_md),
        "output_dir": str(out_dir),
        "style_ref": str(selected_fv_image),
        "style_ref_scope": "採用FV画像の配色・書体の系統と太さ・角丸・線・アイコン・イラスト・ボタンの雰囲気。FVレイアウト、H1サイズ、モチーフは機械的にコピーしない",
        "reference_image_sha256": selected_fv_sha256,
        "generation_surface": "codex-cli-attached-image",
        "page_flow": page_flow,
        "quality_hint": quality,
        "batch_prompt_file": str(batch_prompt_file),
        "individual_prompt_dir": str(individual_prompt_dir),
        "child_run_log": str(batch_child_run_log),
        "image_input_argument": batch_image_input_argument,
        "background_plan": background_plan,
        "content_surface_policy": background_plan.get(
            "content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY
        ),
        "sections": sections,
    }
    prompt_spec_path = task_dir / "prompt-spec.json"
    prompt_spec_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return prompt_spec_path


def read_prompt_spec_sections(prompt_spec_path: Path) -> list[dict]:
    """prompt-spec.json を正本としてセクション配列を読む。"""
    try:
        spec = json.loads(prompt_spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{prompt_spec_path} をJSONとして読めません: {exc}")
    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        fail(f"{prompt_spec_path} に sections がありません")
    return sections


def write_imagegen_tasks(
    project_dir: str,
    page: str = "top",
    input_file: str | None = None,
    no_chain: bool = False,
    chain_images: bool = False,
    quality: str = "high",
    open_output: bool = False,
) -> None:
    if quality != "high":
        fail("工程06の正式mock-up生成は --quality high だけを使用してください")
    base = Path(project_dir)
    input_md = Path(input_file) if input_file else base / "_src" / page / "section-prompts.md"
    out_dir = base / "mockups" / page
    task_dir = base / "_imagegen" / page
    prompt_dir = task_dir / "prompts"
    individual_prompt_dir = prompt_dir / "individual"
    child_run_dir = task_dir / "child-runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    individual_prompt_dir.mkdir(parents=True, exist_ok=True)
    child_run_dir.mkdir(parents=True, exist_ok=True)
    for stale_prompt in prompt_dir.glob("task-*.md"):
        stale_prompt.unlink()
    for stale_task in task_dir.glob("task-*.md"):
        stale_task.unlink()
    for stale_prompt in individual_prompt_dir.glob("*.md"):
        stale_prompt.unlink()

    if not input_md.exists():
        fail(f"{input_md} が見つかりませんでした\n先にプロンプトファイルを保存してから再実行してください。")

    prompts = extract_prompts(input_md)
    if not prompts:
        fail(f"{input_md} に構造化プロンプトが見つかりませんでした")
    background_plan = extract_background_plan(input_md)
    if background_plan is None:
        background_plan = legacy_background_plan(prompts)
    verify_size_policy_handoff(base, page, background_plan.get("size_policy"))
    validate_background_plan(background_plan, prompts)
    content_surface_policy = validate_content_surface_policy(
        background_plan.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY)
    )
    background_plan["content_surface_policy"] = content_surface_policy
    validate_prompt_meta(prompts, base, page, content_surface_policy)

    selected_fv_image = extract_selected_fv_image(base)
    selected_fv_sha256 = sha256_file(selected_fv_image)

    page_flow = extract_page_flow(base, page)
    chain = chain_images and not no_chain
    if chain:
        fail("batch.md標準ルートでは --chain は使えません。直前mock-up参照が必要な場合は修正モードで対応してください。")
    batch_prompt_file = prompt_dir / "batch.md"
    batch_child_run_log = child_run_dir / "batch.log"
    batch_image_input_argument = (
        shell_join(
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-sol",
                "--config",
                'model_reasoning_effort="xhigh"',
                "--cd",
                str(base),
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--add-dir",
                str(base),
                "--image",
                str(selected_fv_image),
                "-",
            ]
        )
        + f" < {shlex.quote(str(batch_prompt_file))} > {shlex.quote(str(batch_child_run_log))} 2>&1"
    )

    manifest: list[dict] = []
    queue_lines = [
        f"# 画像生成タスク — {page}",
        "",
        "このファイルはAPIを使わず、Codex上の画像生成へ渡すためのキューです。",
        "通常モードでは `prompts/batch.md` を1つの子Codexへ渡し、子Codexが `prompts/individual/` の独立プロンプトを1本ずつ `$imagegen` へ渡します。",
        "",
        f"- input: `{input_md}`",
        f"- output_dir: `{out_dir}`",
        f"- style_ref: `{selected_fv_image}`（全タスク）",
        f"- prompt_spec: `{task_dir / 'prompt-spec.json'}`",
        "- generation_surface: `codex-cli-attached-image`",
        "- child_model: `gpt-5.6-sol`",
        "- child_reasoning_effort: `xhigh`",
        "- style_ref_scope: 全タスク（採用FVの配色・書体・角丸・線・アイコン・イラスト・ボタンの雰囲気）",
        f"- reference_image_sha256: `{selected_fv_sha256}`",
        f"- page_flow: {page_flow if page_flow else 'なし'}",
        "- chain: なし（直前mock-up画像は参照しない）",
        f"- quality_hint: `{quality}`",
        "- code_friendly: yes",
        f"- content_surface_policy: `{content_surface_policy}`",
        f"- batch_prompt_file: `{batch_prompt_file}`",
        f"- individual_prompt_dir: `{individual_prompt_dir}`",
        f"- child_run_log: `{batch_child_run_log}`",
        f"- image_input_argument: `{batch_image_input_argument}`",
        "",
    ]

    for item in prompts:
        meta = item["meta"]
        filename = str(meta["expected_output_file"])
        slug = section_slug(Path(filename).stem)
        expected_output = out_dir / filename
        task_file = task_dir / f"task-{item['num']:02d}-{slug}.md"
        individual_prompt_file = individual_prompt_dir / f"{item['num']:02d}-{slug}.md"
        item_style_ref = selected_fv_image
        style_ref_attachment_required = True

        manifest.append(
            {
                "num": item["num"],
                "title": item["title"],
                "task_file": str(task_file),
                "individual_prompt_file": str(individual_prompt_file),
                "expected_output": str(expected_output),
                "target_sections": meta["target_sections"],
                "combine_sections_explicit": meta["combine_sections_explicit"],
                "source_text_exact": meta["source_text_exact"],
                "section_purpose": meta["section_purpose"],
                "background_zone": meta["background_zone"],
                **{key: meta[key] for key in ("height_type", "target_ratio") if key in meta},
                "display_text_exact": meta["display_text_exact"],
                **{key: meta[key] for key in ("decoration_level", "special_direction") if key in meta},
                "content_surface_policy": content_surface_policy,
                "reference_image": None,
                "style_ref": str(item_style_ref),
                "reference_image_sha256": selected_fv_sha256,
                "style_ref_scope": "採用FV画像の配色・書体の系統と太さ・角丸・線・アイコン・イラスト・ボタンの雰囲気。FVレイアウト、H1サイズ、モチーフは機械的にコピーしない",
                "style_ref_attachment_required": style_ref_attachment_required,
                "style_ref_attachment_status": "pending" if style_ref_attachment_required else "not_required",
                "actual_image_input": "pending",
                "image_input_method": "actual_image_input_parameter",
                "generation_surface": "codex-cli-attached-image",
                "requested_image_width": REQUESTED_IMAGE_WIDTH,
                "image_input_argument": batch_image_input_argument,
                "batch_prompt_file": str(batch_prompt_file),
                "child_run_log": str(batch_child_run_log),
                "code_friendly": True,
                "page_flow": page_flow,
                "chain": chain,
            }
        )

    prompt_spec_path = write_prompt_spec(
        task_dir=task_dir,
        page=page,
        mode="normal",
        input_md=input_md,
        out_dir=out_dir,
        selected_fv_image=selected_fv_image,
        selected_fv_sha256=selected_fv_sha256,
        page_flow=page_flow,
        quality=quality,
        batch_prompt_file=batch_prompt_file,
        individual_prompt_dir=individual_prompt_dir,
        batch_child_run_log=batch_child_run_log,
        batch_image_input_argument=batch_image_input_argument,
        background_plan=background_plan,
        sections=manifest,
    )
    manifest = read_prompt_spec_sections(prompt_spec_path)

    batch_prompt_file.write_text(
        build_batch_prompt(selected_fv_image, background_plan, manifest),
        encoding="utf-8",
    )

    for index, item in enumerate(manifest):
        task_file = Path(str(item["task_file"]))
        individual_prompt_file = Path(str(item["individual_prompt_file"]))
        child_run_log = Path(str(item["child_run_log"]))
        individual_prompt_file.write_text(
            build_individual_prompt(
                selected_fv_image,
                background_plan,
                item,
                same_zone_edge_prompt_lines(manifest, index),
            ),
            encoding="utf-8",
        )
        task_file.write_text(
            "\n".join(
                [
                    f"# タスク{int(item['num'])}：{item['title']}",
                    "",
                    f"- expected_output: `{item['expected_output']}`",
                    f"- individual_prompt_file: `{individual_prompt_file}`",
                    f"- prompt_spec: `{prompt_spec_path}`",
                    "- target_sections:",
                    *[f"  - {section}" for section in item["target_sections"]],
                    f"- combine_sections_explicit: {item['combine_sections_explicit']}",
                    f"- section_purpose: `{item['section_purpose']}`",
                    f"- background_zone: `{item['background_zone']}`",
                    *[f"- {key}: `{item[key]}`" for key in ("height_type", "target_ratio") if key in item],
                    *([f"- special_direction: `{item['special_direction']}`"] if "special_direction" in item else []),
                    f"- content_surface_policy: `{item['content_surface_policy']}`",
                    "- reference_image: なし",
                    f"- style_ref: `{item['style_ref']}`",
                    "- style_ref_attachment_required: yes",
                    "- style_ref_attachment_status: pending",
                    "- actual_image_input: pending",
                    "- image_input_method: actual_image_input_parameter",
                    "- generation_surface: codex-cli-attached-image",
                    f"- requested_image_width: {REQUESTED_IMAGE_WIDTH}px",
                    f"- image_input_argument: `{item['image_input_argument']}`",
                    f"- batch_prompt_file: `{batch_prompt_file}`",
                    f"- child_run_log: `{child_run_log}`",
                    f"- reference_image_sha256: `{selected_fv_sha256}`",
                    "- code_friendly: yes",
                    "",
                    "## 表示する文字",
                    "",
                    "```text",
                    str(item["display_text_exact"]),
                    "```",
                    "",
                    "## 参照画像添付チェック",
                    "",
                    "- 標準では `image_input_argument` の一括 `codex exec --image` で採用FV画像を子Codexへ添付する。",
                    "- `view_image_only`、`prompt_mentions_reference`、`直前に表示した画像`、prompt内パスだけでは実画像入力済みにしない。",
                    "- Codex CLIの画像添付が使えない場合、通常生成を実行しない。",
                    "- 生成後、`_imagegen/top/reference-attachment-log.md` に実画像入力証跡を記録する。",
                    "- `child_run_log` と `output_file` が実在し、0バイトでない場合だけ `status: passed` にする。",
                    "",
                    "## 出力チェック",
                    "",
                    f"- 保存するPNGは横幅{REQUESTED_IMAGE_WIDTH}pxを希望値とする。",
                    f"- 横幅が{REQUESTED_IMAGE_WIDTH}pxでない場合は、画像を加工せず原寸で使用し、幅だけを理由に再生成または停止しない。",
                    *([f"- 完成PNGを目標比率 `{item['target_ratio']}` にする。重要な文字・写真・あしらいを切って合わせない。"] if item.get("target_ratio") else []),
                    "- 内容の窮屈さ・不要な空き、文字の一貫性・過度な拡大を目視し、問題があれば自動再生成せず報告する。それ以外の流体表現、文言、構成、セクション間統一の品質検査は行わない。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        queue_lines.extend(
            [
                f"## {int(item['num'])}. {item['title']}",
                "",
                f"- task_file: `{task_file}`",
                f"- expected_output: `{item['expected_output']}`",
                f"- individual_prompt_file: `{individual_prompt_file}`",
                f"- prompt_spec: `{prompt_spec_path}`",
                "- target_sections: " + " / ".join(item["target_sections"]),
                f"- combine_sections_explicit: {item['combine_sections_explicit']}",
                f"- section_purpose: `{item['section_purpose']}`",
                f"- background_zone: `{item['background_zone']}`",
                *[f"- {key}: `{item[key]}`" for key in ("height_type", "target_ratio") if key in item],
                *([f"- special_direction: `{item['special_direction']}`"] if "special_direction" in item else []),
                "- reference_image: なし",
                f"- style_ref: `{item['style_ref']}`",
                "- style_ref_attachment_required: yes",
                "- style_ref_attachment_status: pending",
                "- generation_surface: `codex-cli-attached-image`",
                f"- image_input_argument: `{item['image_input_argument']}`",
                f"- batch_prompt_file: `{batch_prompt_file}`",
                f"- child_run_log: `{child_run_log}`",
                f"- reference_image_sha256: `{selected_fv_sha256}`",
                "- code_friendly: yes",
                "",
            ]
        )

    (task_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (task_dir / "queue.md").write_text("\n".join(queue_lines) + "\n", encoding="utf-8")
    log_lines = [
        "# セクション参照画像添付ログ",
        "",
        "| task | target_sections | reference_image_path | reference_image_sha256 | actual_image_input | image_input_method | generation_surface | image_input_argument | individual_prompt_file | batch_prompt_file | child_run_log | output_file | status |",
        "|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|",
    ]
    for item in manifest:
        log_lines.append(
            "| {task} | {target_sections} | `{reference_image_path}` | `{reference_image_sha256}` | pending | actual_image_input_parameter | codex-cli-attached-image | `{image_input_argument}` | `{individual_prompt_file}` | `{batch_prompt_file}` | `{child_run_log}` | `{output_file}` | pending |".format(
                task=f"task-{int(item['num']):02d}",
                target_sections=" / ".join(item["target_sections"]),
                reference_image_path=item["style_ref"],
                reference_image_sha256=item["reference_image_sha256"],
                image_input_argument=item["image_input_argument"],
                individual_prompt_file=item["individual_prompt_file"],
                batch_prompt_file=item["batch_prompt_file"],
                child_run_log=item["child_run_log"],
                output_file=item["expected_output"],
            )
        )
    log_lines.extend(
        [
            "",
            "## 実画像入力証跡として望ましい状態",
            "- `actual_image_input: yes`",
            "- `image_input_method: actual_image_input_parameter`",
            "- `generation_surface: codex-cli-attached-image`",
            "- `image_input_argument` に `codex exec`、`--image`、採用FV画像パス、`prompts/batch.md` が含まれる",
            "- `individual_prompt_file`、`batch_prompt_file`、`child_run_log`、`output_file` が実在し、0バイトではない",
            "",
            "## Issues に記録する状態",
            "- `view_image_only`",
            "- `prompt_mentions_reference`",
            "- `直前に表示した画像`",
            "- `image_input_argument` に `--image` がない",
            "- `image_input_argument` に `prompts/batch.md` がない",
            "- `child_run_log` 欠落",
            "",
            "上記の Issues があっても、画像が生成済みで、0バイト・破損・真っ白などの明確な生成失敗でなければ、提示を止めない。",
        ]
    )
    (task_dir / "reference-attachment-log.md").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    print("✅ 画像生成タスクを作成しました（API未使用）")
    print(f"   タスク: {task_dir / 'queue.md'}")
    print(f"   prompt spec: {prompt_spec_path}")
    print(f"   batch実行指示: {batch_prompt_file}")
    print(f"   個別プロンプト: {individual_prompt_dir}")
    print(f"   子実行ログ: {batch_child_run_log}")
    print(f"   保存先: {out_dir}")
    print("   次は image_input_argument の codex exec --image でbatch.mdを1つの子Codexへ渡し、individualの各promptを1本ずつ生成してください。")

    print("   mock-up画像を保存後、--preview-only で full_preview.html を作成してください。")
    if open_output:
        subprocess.run(["open", str(task_dir)], check=False)


def read_manifest(project_dir: Path, page: str) -> list[dict]:
    manifest_path = project_dir / "_imagegen" / page / "manifest.json"
    if not manifest_path.exists():
        fail(f"{manifest_path} が見つかりませんでした。通常生成で manifest.json を作成してから実行してください。")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{manifest_path} をJSONとして読めません: {exc}")
    if not isinstance(manifest, list) or not manifest:
        fail(f"{manifest_path} にセクション情報がありません")
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            fail(f"{manifest_path}: {index}件目がオブジェクトではありません")
        if not item.get("expected_output"):
            fail(f"{manifest_path}: {index}件目に expected_output がありません")
        if "target_ratio" in item:
            parse_target_ratio(item["target_ratio"], index)
    return manifest


def edge_band_metrics(path: Path, edge: str) -> dict[str, object]:
    """画像端と少し内側の帯を測り、装飾や別色面の侵入を検出する。"""
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
    except Exception as exc:
        fail(f"画像の接続辺を読み取れません: {path}: {exc}")
    width, height = image.size
    band_px = max(EDGE_BAND_MIN_PX, min(EDGE_BAND_MAX_PX, round(height * EDGE_BAND_RATIO)))
    inset = min(height - band_px, band_px * 4)
    if edge == "top":
        edge_box = (0, 0, width, band_px)
        inner_box = (0, inset, width, min(height, inset + band_px))
    elif edge == "bottom":
        edge_box = (0, height - band_px, width, height)
        inner_bottom = max(band_px, height - inset)
        inner_box = (0, max(0, inner_bottom - band_px), width, inner_bottom)
    else:
        raise ValueError(f"未対応の辺です: {edge}")
    edge_stat = ImageStat.Stat(image.crop(edge_box))
    inner_stat = ImageStat.Stat(image.crop(inner_box))
    edge_mean = [round(float(value), 3) for value in edge_stat.mean[:3]]
    inner_mean = [round(float(value), 3) for value in inner_stat.mean[:3]]
    edge_stddev = [round(float(value), 3) for value in edge_stat.stddev[:3]]
    inner_delta = sum((a - b) ** 2 for a, b in zip(edge_mean, inner_mean)) ** 0.5
    return {
        "band_px": band_px,
        "edge_mean_rgb": edge_mean,
        "edge_stddev_rgb": edge_stddev,
        "max_edge_stddev": round(max(edge_stddev), 3),
        "inner_mean_rgb": inner_mean,
        "edge_to_inner_mean_delta": round(inner_delta, 3),
    }


def inspect_same_zone_edges(project_dir: Path, page: str, manifest: list[dict]) -> dict:
    """同じ背景ゾーンの隣接辺を比較し、JSONレポートを保存する。"""
    checks: list[dict] = []
    for index in range(len(manifest) - 1):
        current = manifest[index]
        following = manifest[index + 1]
        zone_id = str(current.get("background_zone", ""))
        if not zone_id or str(following.get("background_zone", "")) != zone_id:
            continue
        current_path = Path(str(current["expected_output"]))
        following_path = Path(str(following["expected_output"]))
        bottom = edge_band_metrics(current_path, "bottom")
        top = edge_band_metrics(following_path, "top")
        pair_delta = sum(
            (a - b) ** 2
            for a, b in zip(bottom["edge_mean_rgb"], top["edge_mean_rgb"])
        ) ** 0.5
        reasons: list[str] = []
        if float(bottom["max_edge_stddev"]) > EDGE_BAND_MAX_STDDEV:
            reasons.append("上側mock-upの下端に背景面以外の変化がある可能性")
        if float(top["max_edge_stddev"]) > EDGE_BAND_MAX_STDDEV:
            reasons.append("下側mock-upの上端に背景面以外の変化がある可能性")
        if float(bottom["edge_to_inner_mean_delta"]) > EDGE_BAND_MAX_MEAN_DELTA:
            reasons.append("上側mock-upの下端が内側の背景面と大きく異なる")
        if float(top["edge_to_inner_mean_delta"]) > EDGE_BAND_MAX_MEAN_DELTA:
            reasons.append("下側mock-upの上端が内側の背景面と大きく異なる")
        if pair_delta > EDGE_BAND_MAX_MEAN_DELTA:
            reasons.append("隣接する上下端の平均色が大きく異なる")
        checks.append(
            {
                "zone": zone_id,
                "upper_mockup": str(current_path),
                "upper_protected_edge": "bottom",
                "lower_mockup": str(following_path),
                "lower_protected_edge": "top",
                "upper_edge_metrics": bottom,
                "lower_edge_metrics": top,
                "edge_pair_mean_delta": round(pair_delta, 3),
                "status": "failed" if reasons else "passed",
                "reasons": reasons,
            }
        )
    failed = [check for check in checks if check["status"] == "failed"]
    report = {
        "status": "failed" if failed else "passed",
        "method": "same-zone outer-band continuity heuristic",
        "thresholds": {
            "edge_band_ratio": EDGE_BAND_RATIO,
            "max_edge_stddev": EDGE_BAND_MAX_STDDEV,
            "max_mean_delta": EDGE_BAND_MAX_MEAN_DELTA,
        },
        "checks": checks,
    }
    report_path = project_dir / "_imagegen" / page / "edge-continuity-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def generate_full_preview_html(project_dir: Path, page: str) -> Path:
    """manifest の expected_output 順で全体プレビューを作る。"""
    out_dir = project_dir / "mockups" / page
    manifest = read_manifest(project_dir, page)
    images = [Path(str(item["expected_output"])) for item in manifest]
    missing = [img for img in images if not img.exists()]
    if missing:
        fail("manifest の expected_output 画像が未生成です\n" + "\n".join(f"- {img}" for img in missing))
    empty = [img for img in images if img.stat().st_size == 0]
    if empty:
        fail("manifest の expected_output 画像が0バイトです\n" + "\n".join(f"- {img}" for img in empty))

    preview_items: list[Path] = [extract_selected_fv_image(project_dir)]
    preview_items.extend(images)
    width_issues = find_requested_width_mismatches(preview_items)
    if width_issues:
        print(
            "WARNING: 希望幅と異なる完成PNGがあります。画像は加工せず原寸のまま "
            "full_preview.html の作成を続行します。\n"
            + "\n".join(f"- {issue}" for issue in width_issues),
            file=sys.stderr,
        )
    ratio_issues = find_target_ratio_mismatches(manifest)
    if ratio_issues:
        print(
            "WARNING: 再生成後も目標比率と異なる完成PNGがあります。"
            "実寸を記録して full_preview.html の作成を続行します。\n"
            + "\n".join(f"- {issue}" for issue in ratio_issues),
            file=sys.stderr,
        )

    imgs_html = "\n".join(
        f'  <img src="{os.path.relpath(img, out_dir)}" alt="{img.stem}">'
        for img in preview_items
    )
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{page} preview</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #e8e8e8; }}
.wrap {{ display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 24px 0; }}
img {{ width: 100%; max-width: 1440px; display: block; box-shadow: 0 2px 12px rgba(0,0,0,.12); }}
</style>
</head>
<body>
<div class="wrap">
{imgs_html}
</div>
</body>
</html>"""
    html_name = "full_preview.html" if page == "top" else f"{page}_preview.html"
    html_path = out_dir / html_name
    html_path.write_text(html, encoding="utf-8")
    return html_path


def write_preview_only(project_dir: str, page: str = "top", open_output: bool = False) -> None:
    base = Path(project_dir)
    html_path = generate_full_preview_html(base, page)
    manifest = read_manifest(base, page)
    edge_report = inspect_same_zone_edges(base, page, manifest)
    print("✅ full_preview.html を作成しました")
    print(f"   full_preview: {html_path}")
    print(f"   接続辺検査: {edge_report['report_path']}")
    if edge_report["status"] == "failed":
        failed_lines = []
        for check in edge_report["checks"]:
            if check["status"] != "failed":
                continue
            failed_lines.append(
                f"- {Path(check['upper_mockup']).name} 下端 ↔ "
                f"{Path(check['lower_mockup']).name} 上端: "
                + " / ".join(check["reasons"])
            )
        fail(
            "同じ背景ゾーンの接続辺検査に失敗しました。生成済み画像は保持し、"
            "自動再生成せず修正指示を待ってください。\n"
            + "\n".join(failed_lines)
        )
    if open_output:
        subprocess.run(["open", str(html_path)], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="APIを使わず画像生成タスクを作成")
    parser.add_argument("project_dir", help="プロジェクトディレクトリ")
    parser.add_argument("page", nargs="?", default="top", help="ページ名 (default: top)")
    parser.add_argument("--input-file", default=None, help="プロンプト入力ファイル (default: _src/<page>/section-prompts.md)")
    parser.add_argument("--revision", action="store_true", help="_src/<page>/revision-request.md から対象mock-upだけの修正タスクを作る")
    parser.add_argument("--revision-file", default=None, help="修正依頼ファイル (default: _src/<page>/revision-request.md)")
    parser.add_argument("--preview-only", action="store_true", help="manifest.json の expected_output 順で full_preview.html だけを作る")
    parser.add_argument("--chain", action="store_true", help="前画像参照の指示を出す（通常は使わない）")
    parser.add_argument("--no-chain", action="store_true", help="前画像参照の指示を出さない（互換用。通常生成のデフォルト）")
    parser.add_argument("--quality", default="high", choices=["high"], help="正式mock-up用の高品質モード（high固定）")
    parser.add_argument("--open", action="store_true", help="生成後にFinderまたはfull_previewを開く")
    args = parser.parse_args()

    if args.preview_only:
        write_preview_only(
            args.project_dir,
            args.page,
            open_output=args.open,
        )
    elif args.revision:
        write_revision_tasks(
            args.project_dir,
            args.page,
            revision_file=args.revision_file,
            quality=args.quality,
            open_output=args.open,
        )
    else:
        write_imagegen_tasks(
            args.project_dir,
            args.page,
            input_file=args.input_file,
            no_chain=args.no_chain,
            chain_images=args.chain,
            quality=args.quality,
            open_output=args.open,
        )
