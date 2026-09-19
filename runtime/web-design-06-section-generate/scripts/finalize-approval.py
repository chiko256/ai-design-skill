#!/usr/bin/env python3
"""Freeze the user-approved TOP mockups as deterministic source records."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image


REQUESTED_SOURCE_WIDTH = 2880
DEFAULT_TARGET_FRAME_WIDTH = 1440


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_project_path(project_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("manifest expected_output must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def project_relative(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError as exc:
        raise ValueError(f"approved source is outside project root: {path}") from exc


def image_dimensions(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"approved source is missing or empty: {path}")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        raise ValueError(f"approved source is not a readable image: {path}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"approved source dimensions are invalid: {path} ({width}x{height})")
    return width, height


def build_full_preview(
    source_paths: list[Path], output_path: Path, target_frame_width: int
) -> tuple[int, int]:
    opened: list[Image.Image] = []
    resized: list[Image.Image] = []
    try:
        for path in source_paths:
            opened.append(Image.open(path).convert("RGBA"))
        for image in opened:
            scaled_height = max(1, round(image.height * target_frame_width / image.width))
            resized.append(
                image.resize((target_frame_width, scaled_height), Image.Resampling.LANCZOS)
            )
        total_height = sum(image.height for image in resized)
        combined = Image.new("RGBA", (target_frame_width, total_height), (0, 0, 0, 0))
        offset_y = 0
        for image in resized:
            combined.alpha_composite(image, (0, offset_y))
            offset_y += image.height
        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.save(output_path)
        return combined.size
    finally:
        for image in resized:
            image.close()
        for image in opened:
            image.close()


def finalize_approval(
    project_root: Path,
    approval_quote: str,
    target_frame_width: int = DEFAULT_TARGET_FRAME_WIDTH,
    approved_at: str | None = None,
) -> dict[str, object]:
    project_root = project_root.resolve()
    if not approval_quote.strip():
        raise ValueError("approval quote must not be empty")
    if target_frame_width <= 0:
        raise ValueError("target frame width must be positive")

    manifest_path = project_root / "_imagegen" / "top" / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("manifest must contain at least one mockup")

    fv_path = project_root / "mockups" / "top" / "00-fv.png"
    ordered: list[tuple[str, str, Path]] = [("fv", "FV", fv_path)]
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"manifest item {index} must be an object")
        source_path = resolve_project_path(project_root, item.get("expected_output"))
        num = item.get("num", index)
        section_id = f"section-{int(num):02d}" if isinstance(num, int) else f"section-{index:02d}"
        title = str(item.get("title") or source_path.stem)
        ordered.append((section_id, title, source_path))

    entries: list[dict[str, object]] = []
    for section_id, title, path in ordered:
        dimensions = image_dimensions(path)
        entries.append(
            {
                "section_id": section_id,
                "title": title,
                "path": project_relative(project_root, path),
                "sha256": sha256_file(path),
                "dimensions": list(dimensions),
                "source_to_target_scale": target_frame_width / dimensions[0],
            }
        )

    overall_path = project_root / "mockups" / "top" / "full_preview.png"
    overall_dimensions = build_full_preview(
        [item[2] for item in ordered], overall_path, target_frame_width
    )
    timestamp = approved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    record: dict[str, object] = {
        "schema_version": 2,
        "status": "approved",
        "approved_by": "user",
        "approved_at": timestamp,
        "approval_quote": approval_quote,
        "requested_source_width": REQUESTED_SOURCE_WIDTH,
        "source_width_policy": "per_entry_original",
        "target_frame_width": target_frame_width,
        "manifest_path": project_relative(project_root, manifest_path),
        "overall_path": project_relative(project_root, overall_path),
        "overall_sha256": sha256_file(overall_path),
        "overall_dimensions": list(overall_dimensions),
        "entries": entries,
    }

    output_dir = project_root / "_src" / "top"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "approved-source.json"
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    markdown_lines = [
        "# セクションmock-up採用記録",
        "",
        "- 判断: 採用",
        "- 次工程OK: あり",
        "- 正本: `_src/top/approved-source.json`",
        f"- 承認日時: `{timestamp}`",
        f"- Figma目標フレーム幅: `{target_frame_width}px`",
        "- 変換倍率: 採用画像ごとに実寸から算出",
        "",
        "## ユーザー返答",
        f"> {approval_quote}",
        "",
        "## 採用画像",
    ]
    markdown_lines.extend(
        f"- `{entry['path']}` — `{entry['dimensions'][0]}x{entry['dimensions'][1]}px` / "
        f"Figma倍率 `{entry['source_to_target_scale']:.6g}`"
        for entry in entries
    )
    markdown_lines.extend(
        [
            "",
            "## 全体比較用画像",
            f"- `{record['overall_path']}`",
            "",
            "## 次工程",
            "- `web-design-09-rebuild-mockup-in-figma`",
            "",
        ]
    )
    (output_dir / "mockup-approval.md").write_text("\n".join(markdown_lines), encoding="utf-8")
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize approved TOP mockups")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--approval-quote", required=True)
    parser.add_argument("--target-frame-width", type=int, default=DEFAULT_TARGET_FRAME_WIDTH)
    parser.add_argument("--approved-at")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        record = finalize_approval(
            args.project_root,
            args.approval_quote,
            args.target_frame_width,
            args.approved_at,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
