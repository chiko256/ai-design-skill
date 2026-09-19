#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


REFERENCE_NOTE_HEADING = re.compile(r"^##\s+参考サイト\s+(\d{2})\s*$", re.MULTILINE)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() and path.is_file() else ""


def extract_urls(text: str) -> set[str]:
    return set(re.findall(r"https?://[^\s`<>)\"']+", text))


def parse_reference_note_blocks(text: str) -> dict[int, str]:
    matches = list(REFERENCE_NOTE_HEADING.finditer(text))
    blocks: dict[int, str] = {}
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[int(match.group(1))] = text[match.end():next_start]
    return blocks


def previous_input_errors(project: Path) -> list[str]:
    errors: list[str] = []
    brief = read(project / "brief.md")
    if not brief.strip():
        return ["previous gate: missing brief.md"]
    texts = read(project / "texts.md")
    pending_reason = re.search(
        r"^[ \t]*[-*][ \t]*(?:テキスト状態|テキスト未定|後回し理由|未定・後回し理由).*[：:][ \t]*(.+\S)[ \t]*$",
        brief,
        re.MULTILINE,
    )
    if not texts.strip() and not pending_reason:
        errors.append("previous gate: texts.md or explicit text pending/deferred reason is required")
    return errors


def file_errors(project: Path) -> list[str]:
    errors: list[str] = []
    screenshot_paths = sorted((project / "_refs").glob("reference-site-fv-*.png"))
    note_rel = "_fv/reference-note.md"
    note_path = project / note_rel
    note_text = read(note_path)
    if not note_path.exists() or not note_path.is_file():
        errors.append(f"file gate: note file missing: {note_rel}")
        return errors
    if note_path.stat().st_size == 0 or not note_text.strip():
        errors.append(f"file gate: note file is empty: {note_rel}")
        return errors

    no_reference_mode = bool(
        re.search(r"reference_mode:\s*none", note_text, re.IGNORECASE)
        or re.search(r"参考サイト(?:（好き）)?\s*[:：]\s*参考なし", note_text)
    )
    if no_reference_mode:
        if not re.search(r"(?:理由|案件固有の方針|デザイン方針)\s*[:：]\s*\S", note_text):
            errors.append("cross-check gate: no-reference mode requires a reason or project-specific design direction")
        return errors

    if not screenshot_paths:
        errors.append("file gate: no reference FV screenshots found")
    for path in screenshot_paths:
        rel_path = str(path.relative_to(project))
        if not path.exists():
            errors.append(f"file gate: screenshot file missing: {rel_path}")
        elif path.stat().st_size == 0:
            errors.append(f"file gate: screenshot file is empty: {rel_path}")

    blocks = parse_reference_note_blocks(note_text)
    if not blocks:
        errors.append("cross-check gate: note must contain one ## 参考サイト NN block per reference URL")

    for index, path in enumerate(screenshot_paths, start=1):
        rel_path = str(path.relative_to(project))
        block = blocks.get(index)
        label = f"参考サイト {index:02d}"
        if block is None:
            errors.append(f"cross-check gate: note block missing: {label}")
            continue
        if not extract_urls(block):
            errors.append(f"cross-check gate: reference URL not found in {label}")
        if rel_path and rel_path not in block:
            errors.append(f"cross-check gate: screenshot file not found in {label}: {rel_path}")
        if not re.search(r"カラーのトーン:\s*\S", block):
            errors.append(f"cross-check gate: color tone not found in {label}")
        if not re.search(r"雰囲気:\s*\S", block):
            errors.append(f"cross-check gate: mood note not found in {label}")

    return errors


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

    errors.extend(previous_input_errors(project))
    errors.extend(file_errors(project))

    if errors:
        print("STAGE_GATE_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("STAGE_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
