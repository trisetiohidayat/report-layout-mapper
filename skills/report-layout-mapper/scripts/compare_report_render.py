#!/usr/bin/env python3
"""Compare a rendered report PNG against a rectified reference page."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def require_pillow():
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps
    except ImportError as error:
        raise SystemExit("Missing dependency. Install: python3 -m pip install Pillow") from error
    return Image, ImageChops, ImageFilter, ImageOps

def require_cv2_numpy():
    if not importlib.util.find_spec("cv2") or not importlib.util.find_spec("numpy"):
        raise SystemExit("Missing dependency. Install: python3 -m pip install numpy opencv-python")
    import cv2
    import numpy as np

    return cv2, np


def prepare_edge_image(path: Path, size: tuple[int, int] | None = None):
    Image, _ImageChops, ImageFilter, ImageOps = require_pillow()
    image = Image.open(path).convert("L")
    if size and image.size != size:
        image = image.resize(size, resample_filter(Image))
    image = ImageOps.autocontrast(image)
    return image.filter(ImageFilter.FIND_EDGES)

def prepare_structure_image(path: Path, size: tuple[int, int] | None = None):
    Image, _ImageChops, _ImageFilter, ImageOps = require_pillow()
    cv2, np = require_cv2_numpy()
    image = Image.open(path).convert("L")
    if size and image.size != size:
        image = image.resize(size, resample_filter(Image))
    image = ImageOps.autocontrast(image)
    gray = np.asarray(image)
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    height, width = binary.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, width // 80), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, height // 80)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    structure = cv2.bitwise_or(horizontal, vertical)
    structure = cv2.dilate(structure, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)
    return Image.fromarray(structure)


def threshold_mask(image, threshold: int):
    return image.point(lambda value: 255 if value >= threshold else 0, mode="1").convert("L")


def merge_positions(positions: list[int], tolerance: int) -> list[int]:
    if not positions:
        return []
    merged = []
    cluster = [positions[0]]
    for position in positions[1:]:
        if position - cluster[-1] <= tolerance:
            cluster.append(position)
        else:
            merged.append(round(sum(cluster) / len(cluster)))
            cluster = [position]
    merged.append(round(sum(cluster) / len(cluster)))
    return merged


def diff_bbox(mask):
    bbox = mask.getbbox()
    if not bbox:
        return None
    return list(bbox)


def mm_to_px(value_mm: float, size_px: int, size_mm: float) -> int:
    if size_mm <= 0:
        return 1
    return max(1, round(value_mm / size_mm * size_px))


def extract_axis_line_positions(binary, axis: str, min_length_px: int, merge_tolerance_px: int) -> list[int]:
    cv2, _np = require_cv2_numpy()
    if axis == "horizontal":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, min_length_px), 1))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        min_w = min_length_px
        min_h = 1
    elif axis == "vertical":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, min_length_px)))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        min_w = 1
        min_h = min_length_px
    else:
        raise ValueError("axis must be 'horizontal' or 'vertical'")

    contours, _hierarchy = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    positions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < min_w or h < min_h:
            continue
        if axis == "horizontal":
            positions.append(round(y + h / 2))
        else:
            positions.append(round(x + w / 2))
    return merge_positions(sorted(positions), merge_tolerance_px)


def extract_structure_line_positions(
    image,
    origin_mm: tuple[float, float],
    size_mm: tuple[float, float],
    min_line_length_mm: float = 6.0,
    line_merge_tolerance_mm: float = 0.7,
) -> dict:
    cv2, np = require_cv2_numpy()
    array = np.asarray(image.convert("L"))
    _threshold, binary = cv2.threshold(array, 1, 255, cv2.THRESH_BINARY)
    height, width = binary.shape[:2]
    size_w, size_h = size_mm
    min_horizontal_length_px = mm_to_px(min_line_length_mm, width, size_w)
    min_vertical_length_px = mm_to_px(min_line_length_mm, height, size_h)
    x_merge_tolerance_px = max(2, mm_to_px(line_merge_tolerance_mm, width, size_w))
    y_merge_tolerance_px = max(2, mm_to_px(line_merge_tolerance_mm, height, size_h))
    y_positions = extract_axis_line_positions(binary, "horizontal", min_horizontal_length_px, y_merge_tolerance_px)
    x_positions = extract_axis_line_positions(binary, "vertical", min_vertical_length_px, x_merge_tolerance_px)
    origin_x, origin_y = origin_mm
    return {
        "vertical_lines_mm": [round(origin_x + (x / width * size_w), 2) for x in x_positions],
        "horizontal_lines_mm": [round(origin_y + (y / height * size_h), 2) for y in y_positions],
    }


def match_line_positions(reference_lines: list[float], rendered_lines: list[float], tolerance_mm: float) -> dict:
    unmatched_reference = set(range(len(reference_lines)))
    unmatched_rendered = set(range(len(rendered_lines)))
    matched = []
    candidates = sorted(
        (
            abs(rendered - reference),
            reference_index,
            rendered_index,
            reference,
            rendered,
        )
        for reference_index, reference in enumerate(reference_lines)
        for rendered_index, rendered in enumerate(rendered_lines)
        if abs(rendered - reference) <= tolerance_mm
    )
    for _distance, reference_index, rendered_index, reference, rendered in candidates:
        if reference_index not in unmatched_reference or rendered_index not in unmatched_rendered:
            continue
        unmatched_reference.remove(reference_index)
        unmatched_rendered.remove(rendered_index)
        matched.append(
            {
                "reference_mm": reference,
                "rendered_mm": rendered,
                "delta_mm": round(rendered - reference, 2),
                "abs_delta_mm": round(abs(rendered - reference), 2),
            }
        )
    matched.sort(key=lambda line: line["reference_mm"])
    return {
        "matched": matched,
        "missing_reference_lines_mm": [reference_lines[index] for index in sorted(unmatched_reference)],
        "extra_rendered_lines_mm": [rendered_lines[index] for index in sorted(unmatched_rendered)],
        "max_abs_delta_mm": round(max([line["abs_delta_mm"] for line in matched] or [0]), 2),
        "mean_abs_delta_mm": round(sum(line["abs_delta_mm"] for line in matched) / len(matched), 2) if matched else None,
    }


def build_structure_line_diagnostics(
    reference_image,
    rendered_image,
    crop_mm: tuple[float, float, float, float] | None,
    paper_mm: tuple[float, float],
    tolerance_mm: float,
    min_line_length_mm: float,
    line_merge_tolerance_mm: float,
) -> dict:
    if crop_mm:
        origin_mm = (crop_mm[0], crop_mm[1])
        size_mm = (crop_mm[2] - crop_mm[0], crop_mm[3] - crop_mm[1])
    else:
        origin_mm = (0.0, 0.0)
        size_mm = paper_mm
    reference_lines = extract_structure_line_positions(reference_image, origin_mm, size_mm, min_line_length_mm, line_merge_tolerance_mm)
    rendered_lines = extract_structure_line_positions(rendered_image, origin_mm, size_mm, min_line_length_mm, line_merge_tolerance_mm)
    return {
        "tolerance_mm": tolerance_mm,
        "min_line_length_mm": min_line_length_mm,
        "line_merge_tolerance_mm": line_merge_tolerance_mm,
        "reference": reference_lines,
        "rendered": rendered_lines,
        "vertical": match_line_positions(reference_lines["vertical_lines_mm"], rendered_lines["vertical_lines_mm"], tolerance_mm),
        "horizontal": match_line_positions(reference_lines["horizontal_lines_mm"], rendered_lines["horizontal_lines_mm"], tolerance_mm),
    }


def parse_crop_mm(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop-mm requires x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    if x2 <= x1 or y2 <= y1:
        raise ValueError("--crop-mm requires x2>x1 and y2>y1")
    return (x1, y1, x2, y2)


def crop_box_from_mm(
    crop_mm: tuple[float, float, float, float] | None,
    paper_mm: tuple[float, float],
    pixel_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    if not crop_mm:
        return None
    x1, y1, x2, y2 = crop_mm
    paper_w, paper_h = paper_mm
    width, height = pixel_size
    return (
        round(x1 / paper_w * width),
        round(y1 / paper_h * height),
        round(x2 / paper_w * width),
        round(y2 / paper_h * height),
    )


def compare_images(
    reference: Path,
    rendered: Path,
    out_dir: Path,
    threshold: int = 34,
    crop_mm: tuple[float, float, float, float] | None = None,
    paper_mm: tuple[float, float] = (210.0, 297.0),
    mode: str = "edge",
    line_match_tolerance_mm: float = 3.0,
    min_line_length_mm: float = 6.0,
    line_merge_tolerance_mm: float = 0.7,
) -> dict:
    Image, ImageChops, _ImageFilter, _ImageOps = require_pillow()
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode == "edge":
        reference_image = prepare_edge_image(reference)
        rendered_image = prepare_edge_image(rendered, reference_image.size)
    elif mode == "structure":
        reference_image = prepare_structure_image(reference)
        rendered_image = prepare_structure_image(rendered, reference_image.size)
    else:
        raise ValueError("mode must be 'edge' or 'structure'")

    full_size = reference_image.size
    crop_px = crop_box_from_mm(crop_mm, paper_mm, full_size)
    if crop_px:
        reference_image = reference_image.crop(crop_px)
        rendered_image = rendered_image.crop(crop_px)
    line_diagnostics = None
    if mode == "structure":
        line_diagnostics = build_structure_line_diagnostics(
            reference_image,
            rendered_image,
            crop_mm,
            paper_mm,
            line_match_tolerance_mm,
            min_line_length_mm,
            line_merge_tolerance_mm,
        )
    diff = ImageChops.difference(reference_image, rendered_image)
    mask = threshold_mask(diff, threshold)

    width, height = reference_image.size
    mismatch_pixels = sum(1 for value in mask.getdata() if value)
    total_pixels = width * height
    mismatch_ratio = mismatch_pixels / total_pixels if total_pixels else 0.0
    similarity = max(0.0, 1.0 - mismatch_ratio)

    heatmap = Image.new("RGBA", reference_image.size, (255, 255, 255, 0))
    red = Image.new("RGBA", reference_image.size, (255, 0, 0, 150))
    heatmap.paste(red, mask=mask)
    preview = Image.open(rendered).convert("RGBA").resize(full_size, resample_filter(Image))
    if crop_px:
        preview = preview.crop(crop_px)
    else:
        preview = preview.resize(reference_image.size, resample_filter(Image))
    overlay = Image.alpha_composite(preview, heatmap)

    diff_path = out_dir / "diff-mask.png"
    overlay_path = out_dir / "diff-overlay.png"
    metrics_path = out_dir / "compare_metrics.json"
    mask.save(diff_path)
    overlay.save(overlay_path)

    metrics = {
        "reference": str(reference),
        "rendered": str(rendered),
        "pixel_size": [width, height],
        "mode": mode,
        "threshold": threshold,
        "paper_mm": [paper_mm[0], paper_mm[1]],
        "crop_mm": list(crop_mm) if crop_mm else None,
        "crop_px": list(crop_px) if crop_px else None,
        "mismatch_pixels": mismatch_pixels,
        "total_pixels": total_pixels,
        "mismatch_ratio": round(mismatch_ratio, 6),
        "similarity": round(similarity, 6),
        "diff_bbox_px": diff_bbox(mask),
        "diff_mask": str(diff_path),
        "diff_overlay": str(overlay_path),
    }
    if line_diagnostics is not None:
        metrics["structure_line_deltas_mm"] = line_diagnostics
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def resample_filter(Image):
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Rectified reference PNG, usually clean/page-001.png")
    parser.add_argument("--rendered", type=Path, required=True, help="Rendered report PNG converted from generated PDF")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for metrics and diff images")
    parser.add_argument("--mode", choices=["edge", "structure"], default="edge", help="edge compares all visible edges; structure compares long box/table lines")
    parser.add_argument("--threshold", type=int, default=34, help="Edge difference threshold, 0-255")
    parser.add_argument("--crop-mm", help="Optional region x1,y1,x2,y2 in paper millimeters")
    parser.add_argument("--paper-width-mm", type=float, default=210.0)
    parser.add_argument("--paper-height-mm", type=float, default=297.0)
    parser.add_argument(
        "--line-match-tolerance-mm",
        type=float,
        default=3.0,
        help="Structure-mode tolerance for matching reference/rendered lines before reporting missing/extra lines",
    )
    parser.add_argument(
        "--min-line-length-mm",
        type=float,
        default=6.0,
        help="Structure-mode minimum physical line segment length used for line-delta diagnostics",
    )
    parser.add_argument(
        "--line-merge-tolerance-mm",
        type=float,
        default=0.7,
        help="Structure-mode physical tolerance for merging double edges from thick or anti-aliased lines",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.reference.exists():
        print(f"Reference not found: {args.reference}")
        return 2
    if not args.rendered.exists():
        print(f"Rendered image not found: {args.rendered}")
        return 2
    try:
        crop_mm = parse_crop_mm(args.crop_mm)
    except ValueError as error:
        print(str(error))
        return 2
    try:
        metrics = compare_images(
            args.reference,
            args.rendered,
            args.out,
            args.threshold,
            crop_mm=crop_mm,
            paper_mm=(args.paper_width_mm, args.paper_height_mm),
            mode=args.mode,
            line_match_tolerance_mm=args.line_match_tolerance_mm,
            min_line_length_mm=args.min_line_length_mm,
            line_merge_tolerance_mm=args.line_merge_tolerance_mm,
        )
    except ValueError as error:
        print(str(error))
        return 2
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
