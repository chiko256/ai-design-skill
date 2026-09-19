#!/usr/bin/env python3
"""Remove a flat chroma background using an asset-specific route."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path
from statistics import median

from PIL import Image, ImageFilter


CLEAR_DISTANCE = 18.0
OPAQUE_DISTANCE = 155.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove chroma while preserving either line-art holes or filled interiors."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("line-art", "filled-asset", "background-matted"),
        help="line-art removes matching key color globally; filled-asset routes broad chroma removal from the border; background-matted clears only the matching outer background and keeps all remaining pixels opaque.",
    )
    parser.add_argument(
        "--key",
        help="Optional #RRGGBB key color. Without it, the border median is used.",
    )
    parser.add_argument(
        "--preview-dir",
        type=Path,
        help="Write cream and gray background previews for visual QA.",
    )
    parser.add_argument(
        "--defringe-px",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        help="Replace chroma-contaminated edge RGB within 1 to 3 pixels without changing alpha.",
    )
    parser.add_argument(
        "--alpha-cut-px",
        type=int,
        choices=(0, 1),
        default=0,
        help="Make the outermost visible pixel next to transparency fully transparent.",
    )
    parser.add_argument(
        "--fail-on-fringe",
        action="store_true",
        help="Exit with status 2 when key-colored edge pixels exceed --max-fringe-pixels at the requested display width.",
    )
    parser.add_argument(
        "--display-width-px",
        type=int,
        help="Optional final Figma display width. Fringe failure uses a proportionally resized preview when set.",
    )
    parser.add_argument(
        "--max-fringe-pixels",
        type=int,
        default=0,
        help="Allow this many residual fringe pixels in the final-size fringe measurement.",
    )
    return parser.parse_args()


def parse_hex_color(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError("--key must be in #RRGGBB form")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def border_median(image: Image.Image) -> tuple[int, int, int]:
    width, height = image.size
    pixels = image.load()
    border: list[tuple[int, int, int]] = []
    for x in range(width):
        border.append(pixels[x, 0][:3])
        border.append(pixels[x, height - 1][:3])
    for y in range(1, height - 1):
        border.append(pixels[0, y][:3])
        border.append(pixels[width - 1, y][:3])
    return tuple(int(median(channel)) for channel in zip(*border))


def color_distance(rgb: tuple[int, int, int], key: tuple[int, int, int]) -> float:
    return math.sqrt(sum((rgb[i] - key[i]) ** 2 for i in range(3)))


def smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def removal_mask(
    image: Image.Image, key: tuple[int, int, int], mode: str
) -> tuple[list[float], list[bool]]:
    width, height = image.size
    pixels = image.load()
    distances = [
        color_distance(pixels[x, y][:3], key)
        for y in range(height)
        for x in range(width)
    ]
    candidate_distance = (
        CLEAR_DISTANCE if mode == "background-matted" else OPAQUE_DISTANCE
    )
    candidates = [distance < candidate_distance for distance in distances]

    if mode == "line-art":
        return distances, candidates

    routed = [False] * (width * height)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        index = y * width + x
        if not routed[index] and candidates[index]:
            routed[index] = True
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(1, height - 1):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x > 0:
            enqueue(x - 1, y)
        if x + 1 < width:
            enqueue(x + 1, y)
        if y > 0:
            enqueue(x, y - 1)
        if y + 1 < height:
            enqueue(x, y + 1)

    return distances, routed


def despill(
    rgb: tuple[int, int, int], key: tuple[int, int, int], matte: float
) -> tuple[int, int, int]:
    if matte <= 0.02:
        return (0, 0, 0)
    background_share = 1.0 - matte
    return tuple(
        max(0, min(255, round((rgb[i] - background_share * key[i]) / matte)))
        for i in range(3)
    )


def nearest_interior_rgb(
    pixels: object,
    width: int,
    height: int,
    x: int,
    y: int,
    edge_band: bytearray,
    key: tuple[int, int, int],
    source_alpha: int,
    max_radius: int = 16,
) -> tuple[int, int, int] | None:
    minimum_alpha = max(64, min(192, source_alpha))
    for radius in range(1, max_radius + 1):
        best: tuple[int, int, int] | None = None
        best_spatial_distance: int | None = None
        best_key_distance = -1.0
        left = max(0, x - radius)
        right = min(width - 1, x + radius)
        top = max(0, y - radius)
        bottom = min(height - 1, y + radius)
        for sample_y in range(top, bottom + 1):
            for sample_x in range(left, right + 1):
                if max(abs(sample_x - x), abs(sample_y - y)) != radius:
                    continue
                index = sample_y * width + sample_x
                red, green, blue, alpha = pixels[sample_x, sample_y]
                if alpha < minimum_alpha or edge_band[index]:
                    continue
                candidate = (red, green, blue)
                key_distance = color_distance(candidate, key)
                if key_distance < OPAQUE_DISTANCE:
                    continue
                spatial_distance = (sample_x - x) ** 2 + (sample_y - y) ** 2
                if (
                    best_spatial_distance is None
                    or spatial_distance < best_spatial_distance
                    or (
                        spatial_distance == best_spatial_distance
                        and key_distance > best_key_distance
                    )
                ):
                    best = (red, green, blue)
                    best_spatial_distance = spatial_distance
                    best_key_distance = key_distance
        if best is not None:
            return best
    return None


def cut_alpha_edge(
    image: Image.Image, width_px: int
) -> tuple[Image.Image, int, list[bool]]:
    if width_px not in (0, 1):
        raise ValueError("alpha cut width must be 0 or 1 pixel")
    if width_px == 0:
        return image, 0, [False] * (image.width * image.height)

    alpha = image.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    expanded = transparent.filter(ImageFilter.MaxFilter(width_px * 2 + 1))
    alpha_values = bytearray(alpha.tobytes())
    expanded_values = expanded.tobytes()
    changed = 0
    changed_mask = [False] * len(alpha_values)

    for index, (source_alpha, expanded_alpha) in enumerate(
        zip(alpha_values, expanded_values)
    ):
        if source_alpha > 0 and expanded_alpha > 0:
            alpha_values[index] = 0
            changed_mask[index] = True
            changed += 1

    cut = image.copy()
    cut.putalpha(Image.frombytes("L", image.size, bytes(alpha_values)))
    return cut, changed, changed_mask


def defringe_edges(
    image: Image.Image, key: tuple[int, int, int], width_px: int
) -> tuple[Image.Image, int]:
    if width_px not in (0, 1, 2, 3):
        raise ValueError("defringe width must be 0, 1, 2, or 3 pixels")
    if width_px == 0:
        return image, 0

    alpha = image.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    expanded = transparent.filter(ImageFilter.MaxFilter(width_px * 2 + 1))
    alpha_values = alpha.tobytes()
    expanded_values = expanded.tobytes()
    edge_band = bytearray(
        source_alpha > 0 and expanded_alpha > 0
        for source_alpha, expanded_alpha in zip(alpha_values, expanded_values)
    )

    source_pixels = image.load()
    cleaned = image.copy()
    cleaned_pixels = cleaned.load()
    width, height = image.size
    changed = 0

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if not edge_band[index]:
                continue
            red, green, blue, source_alpha = source_pixels[x, y]
            reference = nearest_interior_rgb(
                source_pixels,
                width,
                height,
                x,
                y,
                edge_band,
                key,
                source_alpha,
            )
            if reference is None:
                continue
            current = (red, green, blue)
            if color_distance(current, key) + 8.0 >= color_distance(reference, key):
                continue
            cleaned_pixels[x, y] = (*reference, source_alpha)
            changed += 1

    return cleaned, changed


def remove_residual_key_pixels(
    image: Image.Image,
    key: tuple[int, int, int],
    maximum_distance: float = CLEAR_DISTANCE,
    allowed_mask: list[bool] | None = None,
) -> tuple[Image.Image, int]:
    """Clear visible key pixels, optionally limited to an approved region."""
    cleaned = image.copy()
    pixels = cleaned.load()
    width, height = cleaned.size
    if allowed_mask is not None and len(allowed_mask) != width * height:
        raise ValueError("allowed mask size must match image size")
    removed = 0

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if allowed_mask is not None and not allowed_mask[index]:
                continue
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0 or color_distance((red, green, blue), key) >= maximum_distance:
                continue
            pixels[x, y] = (red, green, blue, 0)
            removed += 1

    return cleaned, removed


def fringe_metrics(
    image: Image.Image,
    key: tuple[int, int, int],
    radius_px: int = 3,
    minimum_alpha: int = 16,
) -> dict[str, int | float | None]:
    alpha = image.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    expanded = transparent.filter(ImageFilter.MaxFilter(radius_px * 2 + 1))
    alpha_values = alpha.tobytes()
    expanded_values = expanded.tobytes()
    pixels = image.load()
    width, height = image.size
    count = 0
    maximum_alpha = 0
    minimum_distance: float | None = None

    for y in range(height):
        for x in range(width):
            index = y * width + x
            source_alpha = alpha_values[index]
            if source_alpha < minimum_alpha or expanded_values[index] == 0:
                continue
            distance = color_distance(pixels[x, y][:3], key)
            if distance >= OPAQUE_DISTANCE:
                continue
            count += 1
            maximum_alpha = max(maximum_alpha, source_alpha)
            minimum_distance = (
                distance
                if minimum_distance is None
                else min(minimum_distance, distance)
            )

    return {
        "residual_fringe_pixels": count,
        "residual_fringe_max_alpha": maximum_alpha,
        "residual_fringe_min_distance": (
            round(minimum_distance, 3) if minimum_distance is not None else None
        ),
    }


def process_image(
    input_path: Path,
    output_path: Path,
    mode: str,
    key_override: tuple[int, int, int] | None = None,
    preview_dir: Path | None = None,
    defringe_px: int = 0,
    alpha_cut_px: int = 0,
    display_width_px: int | None = None,
) -> dict[str, object]:
    if mode not in ("line-art", "filled-asset", "background-matted"):
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "background-matted" and (defringe_px or alpha_cut_px):
        raise ValueError(
            "background-matted does not allow defringe or alpha cut; regenerate the opaque composite instead"
        )
    if display_width_px is not None and display_width_px <= 0:
        raise ValueError("display width must be a positive integer")

    source = Image.open(input_path).convert("RGBA")
    key = key_override or border_median(source)
    distances, routed = removal_mask(source, key, mode)
    output = Image.new("RGBA", source.size)
    source_pixels = source.load()
    output_pixels = output.load()
    width, height = source.size

    if mode == "background-matted":
        for y in range(height):
            for x in range(width):
                index = y * width + x
                red, green, blue, _source_alpha = source_pixels[x, y]
                output_pixels[x, y] = (red, green, blue, 0 if routed[index] else 255)
        alpha_cut = 0
        alpha_cut_mask = [False] * (width * height)
        defringed = 0
        removed_residual_key_pixels = 0
        non_routed_alpha_changes = 0
        fringe = {
            "residual_fringe_pixels": 0,
            "residual_fringe_max_alpha": 0,
            "residual_fringe_min_distance": None,
        }
    else:
        for y in range(height):
            for x in range(width):
                index = y * width + x
                red, green, blue, source_alpha = source_pixels[x, y]
                if not routed[index]:
                    output_pixels[x, y] = (red, green, blue, source_alpha)
                    continue
                normalized = (
                    (distances[index] - CLEAR_DISTANCE)
                    / (OPAQUE_DISTANCE - CLEAR_DISTANCE)
                )
                matte = smoothstep(normalized)
                alpha = round(source_alpha * matte)
                cleaned = despill((red, green, blue), key, matte)
                output_pixels[x, y] = (*cleaned, alpha)

        output, alpha_cut, alpha_cut_mask = cut_alpha_edge(output, alpha_cut_px)
        output, defringed = defringe_edges(output, key, defringe_px)
        residual_allowed_mask = routed if mode == "filled-asset" else None
        output, removed_residual_key_pixels = remove_residual_key_pixels(
            output,
            key,
            allowed_mask=residual_allowed_mask,
        )
        output_alpha = output.getchannel("A").tobytes()
        source_alpha = source.getchannel("A").tobytes()
        non_routed_alpha_changes = (
            sum(
                before != after and not routed[index] and not alpha_cut_mask[index]
                for index, (before, after) in enumerate(zip(source_alpha, output_alpha))
            )
            if mode == "filled-asset"
            else 0
        )
        if non_routed_alpha_changes:
            raise RuntimeError(
                "filled-asset changed alpha outside the border-routed background"
            )
        fringe = fringe_metrics(output, key, radius_px=max(3, defringe_px))
    final_alpha = output.getchannel("A").tobytes()
    transparent = sum(value == 0 for value in final_alpha)
    partial = sum(0 < value < 255 for value in final_alpha)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(output_path)

    if preview_dir:
        preview_dir.mkdir(parents=True, exist_ok=True)
        solid_backgrounds = (
            ("cream", (252, 247, 238, 255)),
            ("gray", (128, 128, 128, 255)),
            ("dark-blue", (22, 45, 82, 255)),
        )
        preview_backgrounds = [
            (name, Image.new("RGBA", output.size, background))
            for name, background in solid_backgrounds
        ]
        checkerboard = Image.new("RGBA", output.size, (238, 238, 238, 255))
        checkerboard_pixels = checkerboard.load()
        checker_size = 16
        for y in range(output.height):
            for x in range(output.width):
                if (x // checker_size + y // checker_size) % 2:
                    checkerboard_pixels[x, y] = (82, 100, 125, 255)
        preview_backgrounds.append(("checkerboard", checkerboard))

        for name, preview in preview_backgrounds:
            preview.alpha_composite(output)
            preview.convert("RGB").save(
                preview_dir / f"{output_path.stem}-{name}.jpg", quality=94
            )

    display_fringe: dict[str, int | float | None] | None = None
    display_size: list[int] | None = None
    if display_width_px is not None:
        display_height_px = max(1, round(output.height * display_width_px / output.width))
        display = output.resize(
            (display_width_px, display_height_px), Image.Resampling.LANCZOS
        )
        display_fringe = fringe_metrics(display, key, radius_px=1)
        display_size = [display_width_px, display_height_px]

    report = {
        "input": str(input_path),
        "output": str(output_path),
        "mode": mode,
        "key": "#{:02X}{:02X}{:02X}".format(*key),
        "size": list(output.size),
        "transparent_pixels": transparent,
        "partial_pixels": partial,
        "alpha_cut_px": alpha_cut_px,
        "alpha_cut_pixels": alpha_cut,
        "defringe_px": defringe_px,
        "defringed_pixels": defringed,
        "removed_residual_key_pixels": removed_residual_key_pixels,
        "non_routed_alpha_changes": non_routed_alpha_changes,
        "display_size": display_size,
        "display_residual_fringe_pixels": (
            display_fringe["residual_fringe_pixels"]
            if display_fringe is not None
            else fringe["residual_fringe_pixels"]
        ),
        "display_residual_fringe_max_alpha": (
            display_fringe["residual_fringe_max_alpha"]
            if display_fringe is not None
            else fringe["residual_fringe_max_alpha"]
        ),
        "display_residual_fringe_min_distance": (
            display_fringe["residual_fringe_min_distance"]
            if display_fringe is not None
            else fringe["residual_fringe_min_distance"]
        ),
        **fringe,
    }
    return report


def main() -> None:
    args = parse_args()
    key_override = parse_hex_color(args.key) if args.key else None
    report = process_image(
        args.input,
        args.output,
        args.mode,
        key_override=key_override,
        preview_dir=args.preview_dir,
        defringe_px=args.defringe_px,
        alpha_cut_px=args.alpha_cut_px,
        display_width_px=args.display_width_px,
    )
    print(json.dumps(report, ensure_ascii=False))
    if args.max_fringe_pixels < 0:
        raise ValueError("max fringe pixels must be zero or greater")
    if (
        args.fail_on_fringe
        and report["display_residual_fringe_pixels"] > args.max_fringe_pixels
    ):
        print(
            "visible chroma fringe remains above the final-size allowance; inspect previews and regenerate once only when the fringe is visible",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
