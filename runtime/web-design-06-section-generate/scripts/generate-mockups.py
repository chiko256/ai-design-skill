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
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

REQUIRED_IMAGE_WIDTH = 1440
REQUIRED_PROMPT_META_KEYS = {
    "target_sections",
    "source_text_exact",
    "section_purpose",
    "background_zone",
    "display_text_exact",
    "special_direction",
    "implementation_direction",
    "expected_output_file",
}
OPTIONAL_PROMPT_META_KEYS = {
    "combine_sections_explicit", "text_scale_profile", "user_instruction",
    "background_expression_level", "decoration_level", "image_shape",
}
LEGACY_PROMPT_META_KEYS = {"background", "top_background", "bottom_background", "height_type", "target_ratio"}
ALLOWED_PROMPT_META_KEYS = (
    REQUIRED_PROMPT_META_KEYS | OPTIONAL_PROMPT_META_KEYS | LEGACY_PROMPT_META_KEYS
)
ART_DIRECTION_REQUIRED_ROLES = ("主役", "組み合わせ方")
DEFAULT_CONTENT_SURFACE_POLICY = "code-friendly-standard"
ALLOWED_CONTENT_SURFACE_POLICIES = {
    "code-friendly-standard",
    "texture-asset-explicit",
}
DEFAULT_TEXT_SCALE_PROFILE = "default-web"
ALLOWED_TEXT_SCALE_PROFILES = {
    "default-web",
    "news-compact",
    "voice-reading",
    "faq-compact",
}
ALLOWED_DECORATION_LEVELS = {
    "あしらい",
    "あしらい少し",
    "なし",
}
DECORATION_PROMPT_LINES = {
    "あしらい": "- あしらい: TOPの雰囲気に合うあしらいを、セクション内で十分な存在感で使用する。",
    "あしらい少し": "- あしらい: TOPの雰囲気に合う小さなあしらいを、控えめに1〜2箇所だけ使用する。",
    "なし": "- あしらい: このセクションには背景のあしらいを入れない。内容を説明する人物・UI・イラストは使用してよい。",
}
ALLOWED_BACKGROUND_EXPRESSION_LEVELS = {"なし", "少し", "しっかり"}
BACKGROUND_EXPRESSION_PROMPT_LINES = {
    "なし": "- 背景演出量: なし。指定された背景面だけを使い、背景図形・線・光・模様を追加しない。",
    "少し": "- 背景演出量: 少し。内容に合う背景表現を控えめに使い、文字と内容の読みやすさを保つ。",
    "しっかり": "- 背景演出量: しっかり。内容に合う背景表現を強めに使い、文字と内容の読みやすさを保つ。",
}
TEXT_SCALE_PROMPT_LINES = {
    "default-web": (),
    "news-compact": (
        "- 文字階層: お知らせ見出しを中心に、日付・カテゴリ・概要を控えめに揃える。少ない件数を文字拡大で埋めない。",
    ),
    "voice-reading": (
        "- 文字階層: 引用・見出し、本文、氏名・属性の順に強弱を付け、読みやすく揃える。少ない件数を文字拡大で埋めない。",
    ),
    "faq-compact": (
        "- 文字階層: 質問と回答の強弱を揃え、コンパクトな縦リストにする。少ない件数を文字拡大で埋めない。",
    ),
}
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

ALLOWED_PAGE_COMMON_META_KEYS = {
    "style", "background_palette", "background_zones", "content_surface_policy", "design_impression",
    "fv_background_expression", "fv_image_shape",
}
STYLE_REF_SCOPE = "採用FVの配色・書体の雰囲気・写真やイラストの色調と質感・ボタンのデザイン。特殊な画像の外形の使用・不使用は、個別の画像の形に記録した方針に従う。"


def validate_content_surface_policy(value: object) -> str:
    policy = str(value or DEFAULT_CONTENT_SURFACE_POLICY).strip()
    if policy not in ALLOWED_CONTENT_SURFACE_POLICIES:
        allowed = " / ".join(sorted(ALLOWED_CONTENT_SURFACE_POLICIES))
        fail(
            "ページ共通計画: content_surface_policy は "
            f"{allowed} のいずれかにしてください: {policy or '未指定'}"
        )
    return policy

def display_text_roles(display_text: str) -> set[str]:
    """`役割: 表示文字` から文字階層の判定に使う役割名だけを取り出す。"""
    roles: set[str] = set()
    for raw_line in display_text.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip()
        if not line or (":" not in line and "：" not in line):
            continue
        role = re.split(r"[:：]", line, maxsplit=1)[0].strip().lower()
        role = re.sub(r"[0-9０-９]+$", "", role)
        if role:
            roles.add(role)
    return roles

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

def has_faq_text_hierarchy(display_text: str) -> bool:
    roles = display_text_roles(display_text)
    has_question = bool(roles & {"q", "質問"})
    has_answer = bool(roles & {"a", "回答"})
    return has_question and has_answer

def inferred_text_scale_profile(item: dict) -> str:
    """セクション名と表示役割から旧入力の文字スケールを補完する。"""
    meta = item["meta"]
    display_text = str(meta.get("display_text_exact", ""))
    label = " ".join(
        [str(item.get("title", "")), *[str(value) for value in meta.get("target_sections", [])]]
    ).lower()
    if has_faq_text_hierarchy(display_text) or re.search(r"\bfaq\b|よくある質問", label):
        return "faq-compact"
    if re.search(r"\bnews\b|お知らせ|新着情報", label):
        return "news-compact"
    if re.search(
        r"(?:お客様|お客さん|患者(?:さま|様)?|利用者|受講者|宿泊者|先輩移住者|ユーザー).*声|体験談|testimonials?|\bvoice\b",
        label,
    ):
        return "voice-reading"
    return DEFAULT_TEXT_SCALE_PROFILE

def validate_text_scale_profile(item: dict) -> str:
    """文字スケールプロファイルを検証し、旧入力ではセクション種別から補完する。"""
    num = int(item["num"])
    meta = item["meta"]
    inferred = inferred_text_scale_profile(item)
    raw = str(meta.get("text_scale_profile", "")).strip()
    profile = raw or inferred
    if profile not in ALLOWED_TEXT_SCALE_PROFILES:
        allowed = " / ".join(sorted(ALLOWED_TEXT_SCALE_PROFILES))
        fail(
            f"プロンプト{num}: text_scale_profile は {allowed} のいずれかにしてください: "
            f"{profile or '未指定'}"
        )
    if inferred != DEFAULT_TEXT_SCALE_PROFILE and profile != inferred:
        fail(
            f"プロンプト{num}: このセクションの text_scale_profile は "
            f"{inferred} にしてください: {profile}"
        )
    meta["text_scale_profile"] = profile
    return profile

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
    """簡易アートディレクションを固定スキーマへ正規化する。"""
    if not isinstance(value, str) or not value.strip():
        fail(f"プロンプト{num}: special_direction が空です")

    parsed: dict[str, str] = {}
    allowed_roles = {*ART_DIRECTION_REQUIRED_ROLES, "特別な演出"}
    for raw_line in value.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip()
        if not line:
            continue
        match = re.fullmatch(r"([^:：]+)\s*[:：]\s*(.+)", line)
        if not match:
            fail(
                f"プロンプト{num}: special_direction は "
                "`主役 / 組み合わせ方` と任意の `特別な演出` だけで記録してください: "
                f"{line}"
            )
        role, direction = match.group(1).strip(), match.group(2).strip()
        if role in {"奥行き", "見せ場"}:  # 保存済み計画から読み込んでも新しいpromptへ転記しない。
            continue
        if role == "統合":  # 保存済み計画の旧項目名。
            role = "組み合わせ方"
        if role not in allowed_roles:
            fail(f"プロンプト{num}: special_direction に未許可の項目があります: {role}")
        if role in parsed:
            fail(f"プロンプト{num}: special_direction の項目が重複しています: {role}")
        if len(direction) > 160:
            fail(f"プロンプト{num}: special_direction の `{role}` は160文字以内にしてください")
        if re.search(r"```|`?(?:source_text_exact|target_sections|expected_output_file|section_purpose)`?", direction):
            fail(f"プロンプト{num}: special_direction の `{role}` に管理メタを入れないでください")
        if re.search(r"\b\d+(?:\.\d+)?\s*px\b|\b(?:x|y)\s*=|\d+\s*カラム|カード幅|余白量|左右比率", direction, re.IGNORECASE):
            fail(f"プロンプト{num}: special_direction の `{role}` に詳細レイアウト指定を入れないでください")
        parsed[role] = direction

    missing = [role for role in ART_DIRECTION_REQUIRED_ROLES if role not in parsed]
    if missing:
        fail(
            f"プロンプト{num}: special_direction に簡易アートディレクションの必須2項目が不足しています: "
            + ", ".join(missing)
        )

    normalized = [f"{role}: {parsed[role]}" for role in ART_DIRECTION_REQUIRED_ROLES]
    if "特別な演出" in parsed and normalize_text(parsed["特別な演出"]) != "なし":
        normalized.append(f"特別な演出: {parsed['特別な演出']}")
    return "\n".join(normalized)

def validate_content_surface_compatibility(
    special_direction: str,
    policy: str,
    num: int,
    field: str = "special_direction",
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
            f"プロンプト{num}: {field} が content_surface_policy=code-friendly-standard "
            "と矛盾しています。紙・布・水彩の表面テクスチャ、破れた縁、不規則な輪郭を除くか、"
            "ユーザーの明示指定がある場合だけ texture-asset-explicit を選んでください"
        )

def validate_implementation_independence(special_direction: str, num: int, field: str = "special_direction") -> None:
    """複数要素の描き直しを前提にする視覚統合を生成前に止める。"""
    normalized = normalize_text(special_direction)
    has_material = IMPLEMENTATION_DEPENDENCY_MATERIAL_PATTERN.search(normalized)
    has_action = IMPLEMENTATION_DEPENDENCY_ACTION_PATTERN.search(normalized)
    has_exemption = IMPLEMENTATION_INDEPENDENCE_EXEMPTION_PATTERN.search(normalized)
    if has_material and has_action and not has_exemption:
        fail(
            f"プロンプト{num}: {field} が実装独立性と矛盾しています。"
            "線・マスク・切り抜き・装飾・合成・コラージュで複数要素を一体化せず、"
            "共通色・書体・文字階層・輪郭・アイコン・余白・反復規則・背景面で関係づけるか、"
            "1要素内で完結する装飾または位置に依存しない独立素材として明示してください"
        )

def decoration_prompt_line(decoration_level: str, protected_edge: str | None) -> str:
    """旧計画のあしらい量を生成文へ渡す。"""
    base = DECORATION_PROMPT_LINES[decoration_level]
    if protected_edge is None:
        return base
    return (
        f"{base} 接続する{protected_edge}から離し、"
        "あしらいはすべて画像内で完結させる。"
    )


def background_expression_prompt_line(level: str) -> str:
    """FVに背景演出がある新規計画だけで使う。"""
    return BACKGROUND_EXPRESSION_PROMPT_LINES[level]


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        fail(f"PNG画像として寸法を確認できません: {path}")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def normalize_png_width(path: Path, required_width: int = REQUIRED_IMAGE_WIDTH) -> None:
    width, height = png_dimensions(path)
    if width <= 0 or height <= 0:
        fail(f"画像寸法が不正です: {path} ({width}x{height})")
    if width == required_width:
        return
    sips = shutil.which("sips")
    if not sips:
        fail(f"{path} の横幅が {width}px です。{required_width}pxへ補正するための sips が見つかりません")
    new_height = max(1, round(height * required_width / width))
    result = subprocess.run(
        [sips, "-z", str(new_height), str(required_width), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"{path} を横幅{required_width}pxへ補正できませんでした。\n{result.stderr.strip()}")
    normalized_width, _ = png_dimensions(path)
    if normalized_width != required_width:
        fail(f"{path} の横幅補正後チェックに失敗しました: {normalized_width}px")
    try:
        with Image.open(path) as normalized:
            normalized.load()
    except (OSError, ValueError) as exc:
        fail(f"横幅補正後の画像を読み取れません: {path}: {exc}")


def normalize_text(value: str) -> str:
    """検証用に空白差分を吸収する。"""
    return re.sub(r"\s+", "", value or "")


def normalize_background(value: str) -> str:
    """単色HEXは説明文によらず同一視し、グラデーションの方向は保持する。"""
    colors = re.findall(r"#[0-9a-fA-F]{6}\b", value)
    if len(colors) == 1 and not re.search(r"グラデーション|gradient", value, re.I):
        return colors[0].lower()
    return normalize_text(value).lower()


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


def validate_image_shape_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"なし", "none"} or value.strip().isdigit():
        fail(f"{label}: 特殊な画像の形は短い非空文字列で記録し、通常は項目を省略してください")
    value = " ".join(value.split())
    if len(value) > 160 or re.search(r"```|\d+(?:\.\d+)?\s*px|\b(?:x|y)\s*=|\d+\s*カラム|左右比率", value, re.I):
        fail(f"{label}: 160文字以内とし、詳細座標やサイズを指定しないでください")
    return value


def validate_style(value: object) -> str:
    """旧計画の観察記録を保持する。個別promptには出さない。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        fail("ページ共通計画: style は文字列で記録してください")
    return " ".join(value.split())


def spacing_prompt_line(impression: str) -> str:
    if impression.strip() in {"力強い", "賑やか", "にぎやか"}:
        return "- 余白: トレンド感ある余白に。"
    return "- 余白: たっぷり余白を使ったデザインに。"


def extract_background_plan(input_md: Path) -> dict | None:
    """section-prompts.md のページ共通計画を読む。旧ファイルでは None を返す。"""
    content = input_md.read_text(encoding="utf-8")
    match = re.search(r"## (?:ページ共通計画|背景計画)\s*\n+```yaml\n([\s\S]+?)```", content)
    if not match:
        return None
    meta = parse_yaml_meta(match.group(1))
    impression = meta.get("design_impression", "")
    if not isinstance(impression, str) or ("design_impression" in meta and not impression.strip()):
        fail("ページ共通計画: design_impression に主となるデザインの印象を1つ記録してください")
    fv_background_expression = meta.get("fv_background_expression")
    if fv_background_expression is not None:
        if not isinstance(fv_background_expression, str) or fv_background_expression.strip() not in {"あり", "なし"}:
            fail("ページ共通計画: fv_background_expression は あり / なし のいずれかにしてください")
        fv_background_expression = fv_background_expression.strip()
    unknown = sorted(set(meta) - ALLOWED_PAGE_COMMON_META_KEYS)
    if unknown:
        fail(
            "ページ共通計画: 許可されていないYAMLメタがあります: "
            + ", ".join(unknown)
        )
    fv_image_shape = validate_image_shape_text(meta["fv_image_shape"], "ページ共通計画: fv_image_shape") if "fv_image_shape" in meta else None
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
        "style": validate_style(meta.get("style")),
        "design_impression": impression.strip(),
        "content_surface_policy": validate_content_surface_policy(meta.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY)),
        **({"fv_background_expression": fv_background_expression} if fv_background_expression is not None else {}),
        **({"fv_image_shape": fv_image_shape} if fv_image_shape is not None else {}),
    }


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
        "style": "",
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
        line = line.strip()
        if line:
            values.append(line)
    return values


def validate_prompt_meta(
    prompts: list[dict],
    project_dir: Path,
    page: str,
    content_surface_policy: str = DEFAULT_CONTENT_SURFACE_POLICY,
    fv_background_expression: str | None = None,
    fv_image_shape: str | None = None,
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
        for key in ["section_purpose", "background_zone", "display_text_exact", "implementation_direction"]:
            value = meta[key]
            if not isinstance(value, str) or not value.strip():
                fail(f"プロンプト{num}: {key} が空です")
            meta[key] = value.strip()
        visible_values = display_text_values(str(meta["display_text_exact"]))
        if not visible_values:
            fail(f"プロンプト{num}: display_text_exact に表示文字がありません")
        normalized_source = normalize_text(str(source_text))
        source_cursor = 0
        for visible_value in visible_values:
            normalized_value = normalize_text(visible_value)
            position = normalized_source.find(normalized_value, source_cursor)
            if position < 0:
                fail(
                    f"プロンプト{num}: display_text_exact の値が source_text_exact に原稿順で見つかりません: {visible_value}"
                )
            source_cursor = position + len(normalized_value)
        user_instruction = meta.get("user_instruction", "")
        if not isinstance(user_instruction, str):
            fail(f"プロンプト{num}: user_instruction はユーザーの個別指定を短い文字列で記録してください")
        meta["user_instruction"] = " ".join(user_instruction.split())
        validate_text_scale_profile(item)
        if fv_background_expression == "あり":
            if "decoration_level" in meta:
                fail(f"プロンプト{num}: 新規計画では decoration_level を使わず background_expression_level を指定してください")
            level = str(meta.get("background_expression_level", "")).strip()
            if level not in ALLOWED_BACKGROUND_EXPRESSION_LEVELS:
                fail(f"プロンプト{num}: background_expression_level は なし / 少し / しっかり のいずれかにしてください")
            meta["background_expression_level"] = level
        elif fv_background_expression == "なし":
            if "background_expression_level" in meta or "decoration_level" in meta:
                fail(f"プロンプト{num}: FV背景演出なしの場合、背景演出量を記録しないでください")
        else:
            if "background_expression_level" in meta:
                fail(f"プロンプト{num}: background_expression_level にはページ共通計画の fv_background_expression が必要です")
            decoration = str(meta.get("decoration_level", "")).strip()
            if decoration not in ALLOWED_DECORATION_LEVELS:
                fail(f"プロンプト{num}: 旧計画の decoration_level が不正です: {decoration}")
            meta["decoration_level"] = decoration
        if "image_shape" in meta:
            if fv_image_shape is None:
                fail(f"プロンプト{num}: image_shape にはページ共通計画の fv_image_shape が必要です")
            meta["image_shape"] = validate_image_shape_text(meta["image_shape"], f"プロンプト{num}: image_shape")
            validate_content_surface_compatibility(meta["image_shape"], content_surface_policy, num, "image_shape")
            validate_implementation_independence(meta["image_shape"], num, "image_shape")
        meta["special_direction"] = validate_special_direction(meta["special_direction"], num)
        validate_content_surface_compatibility(meta["special_direction"], content_surface_policy, num)
        validate_implementation_independence(meta["special_direction"], num)
        validate_content_surface_compatibility(meta["implementation_direction"], content_surface_policy, num, "implementation_direction")
        validate_implementation_independence(meta["implementation_direction"], num, "implementation_direction")
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
    base = Path(project_dir).resolve()
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

    selected_fv_image = extract_selected_fv_image(base)
    generation_log = task_dir / "generation-log.jsonl"

    backup_dir.mkdir(parents=True, exist_ok=True)

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
        image_input_parameters = {
            "referenced_image_paths": [str(backup_file), str(selected_fv_image)]
        }

        final_prompt = "\n\n".join(
            [
                f"1枚目の参照画像 `{backup_file}` は修正前の対象mock-upです。これをベースにしてください。",
                f"2枚目の参照画像 `{selected_fv_image}` は採用FVです。共通スタイルだけを参照してください。",
                "対象外のセクションや画像は変更しないでください。",
                "修正後の画像は expected_output のパスへ保存し、同じ位置のmock-upとして差し替えてください。",
                f"修正後のPNGは横幅{REQUIRED_IMAGE_WIDTH}pxを目安にしてください。レイアウトは1440px幅のWebセクションとして扱い、横に広い別レイアウトにはしないでください。",
                revision_prompt,
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
                    f"- style_ref: `{selected_fv_image}`",
                    "- style_ref_attachment_required: yes",
                    "- style_ref_attachment_status: pending",
                    "- generation_surface: codex-app-imagegen",
                    f"- image_input_parameters: `{json.dumps(image_input_parameters, ensure_ascii=False)}`",
                    f"- generation_log: `{generation_log}`",
                    f"- required_image_width: {REQUIRED_IMAGE_WIDTH}px",
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
                    f"- 保存したPNGの横幅が{REQUIRED_IMAGE_WIDTH}pxであることを確認する。違う場合は横幅{REQUIRED_IMAGE_WIDTH}pxへ補正する。",
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
                "style_ref": str(selected_fv_image),
                "generation_surface": "codex-app-imagegen",
                "image_input_parameters": image_input_parameters,
                "generation_log": str(generation_log),
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
                f"- style_ref: `{selected_fv_image}`",
                f"- image_input_parameters: `{json.dumps(image_input_parameters, ensure_ascii=False)}`",
                f"- generation_log: `{generation_log}`",
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
    print("   次は同じアプリ内担当が各修正プロンプトとimage_input_parametersを画像生成ツールへ1本ずつ渡してください。")

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


def same_background_edges(sections: list[dict], index: int, background_plan: dict) -> tuple[bool, bool]:
    """ゾーン名ではなく、隣接するページ背景で接続辺を決める。"""
    def key(section: dict) -> str:
        palette_id, value, _ = background_zone_details(background_plan, section["background_zone"])
        return normalize_background(value) or palette_id
    current = key(sections[index])
    return (index > 0 and key(sections[index - 1]) == current,
            index + 1 < len(sections) and key(sections[index + 1]) == current)


def same_zone_edge_prompt_lines(sections: list[dict], index: int, background_plan: dict) -> list[str]:
    same_top, same_bottom = same_background_edges(sections, index, background_plan)
    if not same_top and not same_bottom:
        return []
    edge = "上端と下端" if same_top and same_bottom else "上端" if same_top else "下端"
    neighbor = "前のセクションの下端と次のセクションの上端" if same_top and same_bottom else "前のセクションの下端" if same_top else "次のセクションの上端"
    _, background, _ = background_zone_details(background_plan, sections[index]["background_zone"])
    line = (f"- {edge}までページ背景の {background} をそのまま続ける。"
            f"写真・文字・カード・装飾・影・区切り線は{edge}にかけない。"
            f"{neighbor}も同じ背景にし、上下に並べたとき、一続きの背景に見えるようにする。")
    if re.search(r"グラデーション|gradient|→", background, re.I):
        line += " グラデーションは接続位置の色と方向を合わせ、境界で色や明るさをリセットしない。"
    return [line]


def build_individual_prompt(
    background_plan: dict,
    item: dict,
    same_zone_edge_lines: list[str] | None = None,
    *,
    section_start: int,
) -> str:
    _, palette_value, _ = background_zone_details(background_plan, str(item["background_zone"]))
    content_surface_policy = validate_content_surface_policy(
        background_plan.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY)
    )
    text_scale_profile = str(item.get("text_scale_profile", DEFAULT_TEXT_SCALE_PROFILE)).strip()
    if text_scale_profile not in ALLOWED_TEXT_SCALE_PROFILES:
        fail(f"個別prompt: 未対応の text_scale_profile です: {text_scale_profile}")
    direction = str(item["special_direction"]).strip()
    fv_background_expression = background_plan.get("fv_background_expression")
    if fv_background_expression == "あり":
        level = str(item["background_expression_level"]).strip()
        if level not in ALLOWED_BACKGROUND_EXPRESSION_LEVELS:
            fail(f"個別prompt: 未対応の background_expression_level です: {level}")
        background_expression_line = background_expression_prompt_line(level)
    elif fv_background_expression == "なし":
        background_expression_line = None
    else:
        decoration_level = str(item["decoration_level"]).strip()
        if decoration_level not in ALLOWED_DECORATION_LEVELS:
            fail(f"個別prompt: 未対応の decoration_level です: {decoration_level}")
        background_expression_line = decoration_prompt_line(decoration_level, None)
    section_positions = "・".join(str(section_start + offset) for offset in range(len(item["target_sections"])))
    lines = [
        "## 生成対象",
        "",
        f"横幅{REQUIRED_IMAGE_WIDTH}pxのWebデザインの1セクションの画像を制作。",
        f"添付したFVと同じWebページの、FVより下の{section_positions}番目のセクションです。",
        "添付FVは、配色・書体の雰囲気・写真やイラストの色調と質感・ボタンのデザインを参考にする。",
        "各セクションの構成とビジュアルの見せ方は、以下の内容・目的に合わせて設計する。",
        "",
        "## 背景カラー",
        "",
        palette_value,
        "",
        "## 実装可能性",
        "",
        f"- {item['implementation_direction']}",
    ]
    if content_surface_policy == DEFAULT_CONTENT_SURFACE_POLICY:
        lines.append("- カードやパネルには紙・布・水彩の質感、破れた縁、不規則な輪郭を使わない。")
    lines.extend([
        "",
        "## 見せ方",
        "",
        "- 狙い: " + " ".join(item["section_purpose"].split()),
        spacing_prompt_line(background_plan.get("design_impression", "")),
        "- タイトル文字は、画像の左右端からそれぞれ100px以上内側に収める。",
        *TEXT_SCALE_PROMPT_LINES[text_scale_profile],
        *(f"- {line.strip()}" for line in direction.splitlines() if line.strip()),
        *([f"- 画像の形: {item['image_shape']}"] if background_plan.get("fv_image_shape") and item.get("image_shape") else []),
        *([background_expression_line] if background_expression_line else []),
    ])
    if item.get("user_instruction"):
        lines.append("- " + item["user_instruction"])
    if item["combine_sections_explicit"] == "yes":
        lines[2] = f"横幅{REQUIRED_IMAGE_WIDTH}pxのWebデザインの指定された2セクションを1枚の画像として制作。"
    lines.extend(same_zone_edge_lines or [])
    lines.extend([
        "",
        "## 表示文字",
        "",
        "コロンより左は管理ラベル。右側の文言だけを、記載順に表示する。",
        "",
        "```text",
        format_display_text_for_prompt(str(item["display_text_exact"])),
        "```",
    ])
    return "\n".join(lines) + "\n"


def build_batch_prompt(
    selected_fv_image: Path,
    manifest: list[dict],
) -> str:
    lines = [
        "$imagegen",
        "",
        "生成方式（最優先・必須）:",
        "- `prompts/individual/` の個別promptを番号順に読み、各ファイルの全文を1回の `$imagegen` 呼び出しの `prompt` へ1本ずつ渡す。ファイルパスだけを渡さない。",
        f"- {len(manifest)}枚を1回の画像生成へまとめず、1画像につき1プロンプトで生成する。",
        "- 各完成PNGは、必ず `$imagegen` によるAI画像生成で作る。添付FVは全画像のスタイル参照として使う。",
        "- HTML/CSSレンダリング、Playwrightなどのブラウザ撮影、Canvas、SVG、手作業の画像合成を完成mock-upの生成元にしない。",
        "- 日本語文字の正確さを理由に、HTML/CSSやブラウザ撮影へ切り替えない。軽微な文字差は許容する。",
        "- 各個別プロンプトは1回だけ実行し、理由を問わず自動再生成しない。失敗時は生成済み画像を保持して停止する。",
        "",
        "目的:",
        "添付FVから続く1つのWebサイトとして、個別プロンプトからFV下のmock-upを1枚ずつ生成する。",
        "個別プロンプトを画像生成内容の正本とし、このbatchは実行順とページ全体の連続性だけを管理する。",
        "",
        "入力画像:",
        f"採用FV `{selected_fv_image}` を確認し、各画像生成呼び出しの `referenced_image_paths` に毎回渡す。パスをpromptに書くだけでは添付にならない。",
        "",
        "個別prompt全文を変更せず渡す。このbatch・taskの説明をpromptへ追記しない。",
        "",
        "実行と記録:",
        "- 工程05〜06の同じアプリ内担当が直接実行する。別CLIや画像生成専用の子担当は起動しない。",
        "- 各呼び出し直前にワークフローの配置判断を行い、prepare-layout.pyで保存する。FVをページの1枚目として数え、直前2枚の実画像が左テキストで、次が2カラムに適する場合だけ未実行promptを右テキストへ更新する。明示指定を優先する。",
        "- manifestのprompt_sha256を個別ファイルと照合し、image_input_parametersを実際の参照画像引数へ渡す。FVを目視するだけでは添付済みにしない。",
        "- 実際に返った画像をexpected_outputへ保存する。返却元を推測しない。横幅は1440pxへ等比補正し、内容を切り詰めない。",
        "- generation-log.jsonlへタスク番号（task_num）・promptパス・実参照画像引数・返却元・保存先・試行回数・結果を1呼び出し1行で追記する。呼び出しIDがあれば併記する。",
        "- 実入力と保存PNGの読取を確認してから、reference-attachment-log.mdとmanifestの添付状態を更新する。失敗も記録し、未確認の成功状態を作らない。",
        "",
    ]
    lines.append("個別プロンプト実行順:")
    for item in manifest:
        lines.extend(
            [
                "",
                f"mock-up {int(item['num'])}",
                f"個別プロンプト: `{item['individual_prompt_file']}`",
                f"保存先: `{item['expected_output']}`",
                f"背景ゾーン: {item['background_zone']}",
            ]
        )

    lines.extend(
        [
            "",
            "完了条件:",
            f"- {len(manifest)}本の個別プロンプトを番号順に1本ずつ実行し、{len(manifest)}枚すべてを指定の保存先へ保存する。",
            f"- 各PNGの横幅が{REQUIRED_IMAGE_WIDTH}pxか確認する。違う場合は可能な範囲で補正する。",
            "- 画像は原則1セクション1枚に分ける。2セクションを1枚にするのは、上で明示したmock-upだけにする。",
            "- 生成後は背景のつながりと原稿・雰囲気を確認し、気づいた差を報告する。生成済みpromptへ指示を追加せず、自動再生成しない。",
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
    generation_log: Path,
    image_input_parameters: dict,
    background_plan: dict,
    sections: list[dict],
) -> Path:
    """画像生成へ渡す前の構造化prompt正本を保存する。"""
    spec = {
        "schema_version": 12,
        "page": page,
        "mode": mode,
        "source": str(input_md),
        "output_dir": str(out_dir),
        "style_ref": str(selected_fv_image),
        "style_ref_scope": STYLE_REF_SCOPE,
        "reference_image_sha256": selected_fv_sha256,
        "generation_surface": "codex-app-imagegen",
        "page_flow": page_flow,
        "quality_hint": quality,
        "batch_prompt_file": str(batch_prompt_file),
        "individual_prompt_dir": str(individual_prompt_dir),
        "generation_log": str(generation_log),
        "image_input_parameters": image_input_parameters,
        "background_plan": background_plan,
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
    base = Path(project_dir).resolve()
    input_md = Path(input_file) if input_file else base / "_src" / page / "section-prompts.md"
    out_dir = base / "mockups" / page
    task_dir = base / "_imagegen" / page
    prompt_dir = task_dir / "prompts"
    individual_prompt_dir = prompt_dir / "individual"
    existing_log = task_dir / "generation-log.jsonl"
    if existing_log.exists() and existing_log.stat().st_size:
        fail(f"生成記録があるため通常キューを上書きできません: {existing_log}\n既存画像を保持し、提示には --preview-only、修正には --revision を使ってください。")
    out_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    individual_prompt_dir.mkdir(parents=True, exist_ok=True)
    if not input_md.exists():
        fail(f"{input_md} が見つかりませんでした\n先にプロンプトファイルを保存してから再実行してください。")

    prompts = extract_prompts(input_md)
    if not prompts:
        fail(f"{input_md} に構造化プロンプトが見つかりませんでした")
    if any("prompt_file" in item["meta"] for item in prompts):
        fail("個別JSONからの再開は対応していません。工程05の計画からMarkdown用のsection-prompts.mdを作成してください。既存JSON・画像は変更しません。")
    background_plan = extract_background_plan(input_md)
    if background_plan is None:
        background_plan = legacy_background_plan(prompts)
    validate_background_plan(background_plan, prompts)
    validate_prompt_meta(
        prompts, base, page,
        background_plan.get("content_surface_policy", DEFAULT_CONTENT_SURFACE_POLICY),
        background_plan.get("fv_background_expression"),
        background_plan.get("fv_image_shape"),
    )
    if any(individual_prompt_dir.glob("*.json")):
        fail("個別JSONが残っている作業フォルダは上書きしません。新規Markdown生成には別の作業フォルダを使ってください。")

    for stale_prompt in prompt_dir.glob("task-*.md"):
        stale_prompt.unlink()
    for stale_task in task_dir.glob("task-*.md"):
        stale_task.unlink()
    for stale_prompt in individual_prompt_dir.glob("*.md"):
        stale_prompt.unlink()

    selected_fv_image = extract_selected_fv_image(base)
    selected_fv_sha256 = sha256_file(selected_fv_image)

    page_flow = extract_page_flow(base, page)
    chain = chain_images and not no_chain
    if chain:
        fail("batch.md標準ルートでは --chain は使えません。直前mock-up参照が必要な場合は修正モードで対応してください。")
    batch_prompt_file = prompt_dir / "batch.md"
    generation_log = task_dir / "generation-log.jsonl"
    image_input_parameters = {"referenced_image_paths": [str(selected_fv_image)]}

    manifest: list[dict] = []
    queue_lines = [
        f"# 画像生成タスク — {page}",
        "",
        "実行順の索引。手順はbatch、入力引数・hash・添付状態・保存先はmanifestを正本とする。",
        "",
        f"- manifest: `{task_dir / 'manifest.json'}`",
        f"- prompt_spec: `{task_dir / 'prompt-spec.json'}`",
        f"- batch_prompt_file: `{batch_prompt_file}`",
        f"- generation_log: `{generation_log}`",
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
                "display_text_exact": meta["display_text_exact"],
                "user_instruction": meta["user_instruction"],
                **{key: meta[key] for key in (
                    "text_scale_profile", "background_expression_level", "decoration_level",
                    "special_direction", "implementation_direction", "image_shape",
                ) if key in meta},
                "prompt_format": "markdown",
                "reference_image": None,
                "style_ref": str(item_style_ref),
                "reference_image_sha256": selected_fv_sha256,
                "style_ref_scope": STYLE_REF_SCOPE,
                "style_ref_attachment_required": style_ref_attachment_required,
                "style_ref_attachment_status": "pending" if style_ref_attachment_required else "not_required",
                "actual_image_input": "pending",
                "image_input_method": "actual_image_input_parameter",
                "generation_surface": "codex-app-imagegen",
                "required_image_width": REQUIRED_IMAGE_WIDTH,
                "image_input_parameters": image_input_parameters,
                "batch_prompt_file": str(batch_prompt_file),
                "generation_log": str(generation_log),
                "page_flow": page_flow,
                "chain": chain,
            }
        )

    serialized_prompts: dict[int, str] = {}
    section_start = 1
    for index, item in enumerate(manifest):
        serialized = build_individual_prompt(
            background_plan, item, same_zone_edge_prompt_lines(manifest, index, background_plan),
            section_start=section_start,
        )
        section_start += len(item["target_sections"])
        serialized_prompts[item["num"]] = serialized
        item["prompt_sha256"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

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
        generation_log=generation_log,
        image_input_parameters=image_input_parameters,
        background_plan=background_plan,
        sections=manifest,
    )
    manifest = read_prompt_spec_sections(prompt_spec_path)

    batch_prompt_file.write_text(
        build_batch_prompt(selected_fv_image, manifest),
        encoding="utf-8",
    )

    for index, item in enumerate(manifest):
        task_file = Path(str(item["task_file"]))
        individual_prompt_file = Path(str(item["individual_prompt_file"]))
        generation_log = Path(str(item["generation_log"]))
        individual_prompt_file.write_text(serialized_prompts[item["num"]], encoding="utf-8")
        if sha256_file(individual_prompt_file) != item["prompt_sha256"]:
            fail(f"プロンプト{item['num']}: 保存したpromptのハッシュが一致しません")
        task_file.write_text(
            "\n".join(
                [
                    f"# タスク{int(item['num'])}：{item['title']}",
                    "",
                    f"- manifest: `{task_dir / 'manifest.json'}` / num: {item['num']}",
                    f"- individual_prompt_file: `{individual_prompt_file}`",
                    f"- expected_output: `{item['expected_output']}`",
                    f"- batch_prompt_file: `{batch_prompt_file}`",
                    "",
                    "参照画像引数・hash・状態はmanifestの該当項目を確認し、batchの手順で実行する。",
                    "表示文言は個別promptに保持し、この索引へ転記しない。",
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
                f"- individual_prompt_file: `{individual_prompt_file}`",
                f"- expected_output: `{item['expected_output']}`",
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
        "| task | target_sections | reference_image_path | reference_image_sha256 | actual_image_input | image_input_method | generation_surface | image_input_parameters | individual_prompt_file | batch_prompt_file | generation_log | output_file | status |",
        "|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|",
    ]
    for item in manifest:
        log_lines.append(
            "| {task} | {target_sections} | `{reference_image_path}` | `{reference_image_sha256}` | pending | actual_image_input_parameter | codex-app-imagegen | `{image_input_parameters}` | `{individual_prompt_file}` | `{batch_prompt_file}` | `{generation_log}` | `{output_file}` | pending |".format(
                task=f"task-{int(item['num']):02d}",
                target_sections=" / ".join(item["target_sections"]),
                reference_image_path=item["style_ref"],
                reference_image_sha256=item["reference_image_sha256"],
                image_input_parameters=json.dumps(item["image_input_parameters"], ensure_ascii=False),
                individual_prompt_file=item["individual_prompt_file"],
                batch_prompt_file=item["batch_prompt_file"],
                generation_log=item["generation_log"],
                output_file=item["expected_output"],
            )
        )
    log_lines.extend(
        [
            "",
            "## 実画像入力証跡として望ましい状態",
            "- `actual_image_input: yes`",
            "- `image_input_method: actual_image_input_parameter`",
            "- `generation_surface: codex-app-imagegen`",
            "- 各呼び出しの `image_input_parameters.referenced_image_paths` に採用FVの絶対パスが含まれる",
            "- `individual_prompt_file`、`batch_prompt_file`、`generation_log`、`output_file` が実在し、0バイトではない",
            "",
            "## Issues に記録する状態",
            "- `view_image_only`",
            "- `prompt_mentions_reference`",
            "- `直前に表示した画像`",
            "- 実際の画像生成呼び出しに `referenced_image_paths` がない",
            "- 参照画像引数に採用FVが含まれない",
            "- `generation_log` 欠落",
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
    print(f"   画像生成ログ: {generation_log}")
    print(f"   保存先: {out_dir}")
    print("   次はアプリ内の同じ担当がbatch.mdを読み、各個別prompt全文を無変更で、image_input_parametersを実画像入力として画像生成ツールへ1本ずつ渡してください。")

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
    return manifest


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
    for image in preview_items:
        try:
            with Image.open(image) as source:
                if source.format != "PNG":
                    fail(f"PNG画像ではありません: {image}")
                source.load()
        except (OSError, ValueError) as exc:
            fail(f"画像として読み取れません: {image}: {exc}")
    for image in images:
        normalize_png_width(image)

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
.wrap {{ display: flex; flex-direction: column; width: 100%; max-width: 1440px; margin: 24px auto; gap: 0; }}
img {{ width: 100%; height: auto; display: block; }}
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
    print("✅ full_preview.html を作成しました")
    print(f"   full_preview: {html_path}")
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
