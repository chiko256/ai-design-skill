#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
VISUAL_MODES = {"illustration_only", "illustration_primary", "mixed", "photography"}
FIGMA_ACCESS_PERMISSION_SCOPE = "指定Figmaへのアクセスと制作素材のアップロード"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def extract_urls(text: str) -> set[str]:
    return set(re.findall(r"https?://[^\s`<>)\"']+", text))


def field_value(text: str, label: str) -> str:
    pattern = re.compile(rf"^[ \t]*[-*][ \t]*{re.escape(label)}[ \t]*[:：][ \t]*(.*)$", re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def is_unresolved(value: str) -> bool:
    normalized = value.strip()
    return normalized in {"", "未定", "なし", "未確認", "空欄"}


def brief_errors(brief: str) -> list[str]:
    errors: list[str] = []
    if not brief.strip():
        return ["brief gate: missing brief.md"]

    required_fields = [
        "業種",
        "ターゲット",
        "ページ構成",
        "印象・ブランドイメージ",
        "visual_mode",
        "ビジュアルスタイル（写真・イラスト）",
    ]
    for label in required_fields:
        value = field_value(brief, label)
        if is_unresolved(value):
            errors.append(f"brief gate: brief.md missing {label}")

    reference_value = field_value(brief, "参考サイト（好き）")
    if is_unresolved(reference_value) or reference_value == "参考なし":
        errors.append("brief gate: 参考サイト（好き） requires at least one URL")
    elif not extract_urls(reference_value):
        errors.append("brief gate: 参考サイト（好き） must contain at least one URL")

    color_value = field_value(brief, "ブランドカラーHEX")
    color_pending = bool(re.match(r"^未確定(?:$|[\s（(、:：])", color_value))
    if color_value and not color_pending and not is_unresolved(color_value) and not any(HEX_COLOR.match(value) for value in re.split(r"[,、\s]+", color_value) if value):
        errors.append("brief gate: ブランドカラーHEX must contain at least one #RRGGBB color")

    visual_mode = field_value(brief, "visual_mode")
    if visual_mode and visual_mode not in VISUAL_MODES:
        errors.append("brief gate: visual_mode must be illustration_only, illustration_primary, mixed, or photography")

    permission_state = field_value(brief, "許可状態")
    if permission_state != "許可済み":
        errors.append("brief gate: Figma access permission state must be 許可済み")

    permission_text = field_value(brief, "確認原文")
    if is_unresolved(permission_text):
        errors.append("brief gate: 確認原文 must record the user's permission response")

    permission_scope = field_value(brief, "許可範囲")
    if permission_scope != FIGMA_ACCESS_PERMISSION_SCOPE:
        errors.append(
            "brief gate: 許可範囲 must cover specified Figma access and asset upload"
        )
    return errors


def text_state_errors(project: Path, brief: str) -> list[str]:
    texts_path = project / "texts.md"
    if texts_path.exists() and texts_path.is_file() and read(texts_path).strip():
        return []

    pattern = re.compile(
        r"^[ \t]*[-*][ \t]*(?:`?texts\.md`?|テキスト状態|テキスト未定|後回し理由|未定・後回し理由|テキスト未定/後回し理由).*[：:][ \t]*(.+\S)[ \t]*$",
        re.MULTILINE,
    )
    if pattern.search(brief):
        return []

    return ["text gate: texts.md or explicit text pending/deferred reason is required"]


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify-stage-gates.py <PROJECT_DIR>")
        return 2

    project = Path(sys.argv[1])
    errors: list[str] = []
    if not project.exists() or not project.is_dir():
        print("STAGE_GATE_FAIL")
        print("- project gate: PROJECT_DIR does not exist")
        return 1

    brief = read(project / "brief.md")
    errors.extend(brief_errors(brief))
    errors.extend(text_state_errors(project, brief))

    if errors:
        print("STAGE_GATE_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("STAGE_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
