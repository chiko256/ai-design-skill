#!/usr/bin/env python3
"""Validate FV prompts, reference assignments, and 16:9 image outputs."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


OUTPUT_SPEC = "横長16:9の画像にする。"
REFERENCE_TEXTS = (
    "添付した参考画像から取り入れる特徴は、",
    "添付した参考画像の雰囲気、情報密度、視線誘導、余白を参考にし",
)
TARGET_ASPECT_RATIO = 16 / 9
ASPECT_RATIO_TOLERANCE = 0.005
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png"}
FORBIDDEN_PROMPT_TEXT = (
    "内部ID",
    "検査ルール",
    "選定理由",
    "保存先",
    "ファイル名",
)


def jpeg_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        if file.read(2) != b"\xff\xd8":
            raise ValueError("JPEGではありません")

        while True:
            marker_start = file.read(1)
            if not marker_start:
                break
            if marker_start != b"\xff":
                continue

            marker = file.read(1)
            while marker == b"\xff":
                marker = file.read(1)
            if not marker:
                break

            code = marker[0]
            if code in {0xD8, 0xD9} or 0xD0 <= code <= 0xD7:
                continue

            length_bytes = file.read(2)
            if len(length_bytes) != 2:
                break
            length = struct.unpack(">H", length_bytes)[0]
            if length < 2:
                break

            if code in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                data = file.read(length - 2)
                if len(data) < 5:
                    break
                height, width = struct.unpack(">HH", data[1:5])
                return width, height

            file.seek(length - 2, 1)

    raise ValueError("JPEG寸法を取得できません")


def image_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(header) < 24 or header[12:16] != b"IHDR":
            raise ValueError("PNG寸法を取得できません")
        return struct.unpack(">II", header[16:24])

    if header.startswith(b"\xff\xd8"):
        return jpeg_size(path)

    raise ValueError("対応するPNGまたはJPEGではありません")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--prompt", action="append", default=[], type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path)
    parser.add_argument("--reference-option", action="append", default=[], type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []

    if len(args.prompt) != args.expected_count:
        errors.append(
            f"プロンプト数: expected={args.expected_count}, actual={len(args.prompt)}"
        )
    if len(args.image) != args.expected_count:
        errors.append(
            f"画像数: expected={args.expected_count}, actual={len(args.image)}"
        )

    reference_options = set(args.reference_option)
    invalid_options = sorted(
        index
        for index in reference_options
        if index < 1 or index > args.expected_count
    )
    if invalid_options:
        errors.append(f"参考画像の案番号が範囲外: {invalid_options}")

    for index, path in enumerate(args.prompt, start=1):
        if not path.is_file():
            errors.append(f"案{index}: プロンプトがない: {path}")
            continue

        text = path.read_text(encoding="utf-8")
        count = text.count(OUTPUT_SPEC)
        if count != 1:
            errors.append(f"案{index}: 出力仕様の出現回数={count}")

        found = [item for item in FORBIDDEN_PROMPT_TEXT if item in text]
        if found:
            errors.append(f"案{index}: 内部情報が混入: {', '.join(found)}")

        has_reference_text = any(marker in text for marker in REFERENCE_TEXTS)
        should_have_reference = index in reference_options
        if has_reference_text != should_have_reference:
            errors.append(f"案{index}: 参考画像の割り当てとプロンプトが不一致")

    for index, path in enumerate(args.image, start=1):
        if not path.is_file():
            errors.append(f"案{index}: 画像がない: {path}")
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            errors.append(f"案{index}: PNGまたはJPGではない: {path}")
            continue
        try:
            size = image_size(path)
        except ValueError as error:
            errors.append(f"案{index}: {error}: {path}")
            continue
        width, height = size
        aspect_ratio = width / height
        relative_error = abs(aspect_ratio - TARGET_ASPECT_RATIO) / TARGET_ASPECT_RATIO
        if width <= height or relative_error > ASPECT_RATIO_TOLERANCE:
            errors.append(
                f"案{index}: 画像比率={width}x{height} ({aspect_ratio:.4f})"
            )

    if errors:
        for error in errors:
            print(f"NG: {error}")
        return 1

    print("OK: FV出力の自動検査に合格しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
