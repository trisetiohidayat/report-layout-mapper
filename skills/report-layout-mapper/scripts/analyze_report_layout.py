#!/usr/bin/env python3
"""Create grid overlays and coordinate manifests for report template references."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


COORDINATE_SYSTEM = "normalized_0_1000_top_left"
PAPER_SIZES_MM = {
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "legal": (215.9, 355.6),
    "letter": (215.9, 279.4),
}


@dataclass
class Element:
    id: str
    type: str
    bbox_px: list[int]
    bbox_norm: list[int]
    bbox_mm: list[float]
    confidence: float
    text: str | None = None

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "type": self.type,
            "bbox_px": self.bbox_px,
            "bbox_norm": self.bbox_norm,
            "bbox_mm": self.bbox_mm,
            "confidence": self.confidence,
        }
        if self.text:
            data["text"] = self.text
        return data


@dataclass
class PageAnalysis:
    page: int
    pixel_size: tuple[int, int]
    paper_mm: tuple[float, float]
    elements: list[Element]
    preprocess: dict | None = None
    structural_grid: dict | None = None

    def to_dict(self) -> dict:
        data = {
            "page": self.page,
            "pixel_size": list(self.pixel_size),
            "paper_mm": list(self.paper_mm),
            "elements": [element.to_dict() for element in self.elements],
        }
        if self.preprocess:
            data["preprocess"] = self.preprocess
        if self.structural_grid:
            data["structural_grid"] = self.structural_grid
        return data


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def has_executable(name: str) -> bool:
    return shutil.which(name) is not None


def missing_dependency_messages(
    detect: str,
    preprocess: str = "none",
    has_module: Callable[[str], bool] = has_module,
    has_executable: Callable[[str], bool] = has_executable,
) -> list[str]:
    messages: list[str] = []
    base_missing = [name for name in ("fitz", "PIL") if not has_module(name)]
    if base_missing:
        messages.append("python3 -m pip install PyMuPDF Pillow")

    if preprocess == "document":
        cv_missing = [name for name in ("numpy", "cv2") if not has_module(name)]
        if cv_missing:
            messages.append("python3 -m pip install numpy opencv-python")

    if detect == "full":
        cv_missing = [name for name in ("numpy", "cv2") if not has_module(name)]
        if cv_missing:
            messages.append("python3 -m pip install numpy opencv-python")

    return list(dict.fromkeys(messages))


def resolve_paper_mm(paper: str, width_mm: float | None, height_mm: float | None) -> tuple[float, float]:
    if paper == "custom":
        if not width_mm or not height_mm:
            raise ValueError("--paper custom requires --paper-width-mm and --paper-height-mm")
        return (float(width_mm), float(height_mm))
    return PAPER_SIZES_MM[paper]


def paper_pixel_size(paper_mm: tuple[float, float], dpi: int) -> tuple[int, int]:
    width_mm, height_mm = paper_mm
    return (round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi))


def pixel_bbox_to_norm(bbox: Iterable[float], width: int, height: int, grid_size: int = 1000) -> list[int]:
    x1, y1, x2, y2 = bbox
    return [
        round(x1 / width * grid_size),
        round(y1 / height * grid_size),
        round(x2 / width * grid_size),
        round(y2 / height * grid_size),
    ]


def norm_bbox_to_mm(bbox_norm: Iterable[float], paper_mm: tuple[float, float]) -> list[float]:
    x1, y1, x2, y2 = bbox_norm
    paper_w, paper_h = paper_mm
    return [
        round(x1 / 1000 * paper_w, 2),
        round(y1 / 1000 * paper_h, 2),
        round(x2 / 1000 * paper_w, 2),
        round(y2 / 1000 * paper_h, 2),
    ]


def px_bbox_to_mm(bbox_px: Iterable[float], width: int, height: int, paper_mm: tuple[float, float]) -> list[float]:
    return norm_bbox_to_mm(pixel_bbox_to_norm(bbox_px, width, height), paper_mm)


def ensure_dirs(out_dir: Path) -> dict[str, Path]:
    dirs = {
        "raw": out_dir / "raw",
        "clean": out_dir / "clean",
        "annotated": out_dir / "annotated",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def render_source_pages(source: Path, out_dir: Path, dpi: int) -> list[Path]:
    from PIL import Image

    raw_dir = ensure_dirs(out_dir)["raw"]
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        import fitz

        doc = fitz.open(source)
        image_paths: list[Path] = []
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            target = raw_dir / f"page-{index:03d}.png"
            pix.save(target)
            image_paths.append(target)
        return image_paths

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        target = raw_dir / "page-001.png"
        with Image.open(source) as image:
            image.convert("RGB").save(target)
        return [target]

    raise ValueError(f"Unsupported source type: {source.suffix}")


def order_points(points) -> list[list[float]]:
    ordered = sorted([[float(x), float(y)] for x, y in points], key=lambda point: (point[1], point[0]))
    top = sorted(ordered[:2], key=lambda point: point[0])
    bottom = sorted(ordered[2:], key=lambda point: point[0])
    return [top[0], top[1], bottom[1], bottom[0]]


def parse_document_corners(value: str | None):
    if not value:
        return None
    points = []
    for pair in value.split(";"):
        x, y = pair.split(",", 1)
        points.append((float(x), float(y)))
    if len(points) != 4:
        raise ValueError("--document-corners requires four x,y pairs")
    return order_points(points)


def find_document_corners(image_path: Path):
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")
    ratio = image.shape[0] / 900.0
    resized = cv2.resize(image, (round(image.shape[1] / ratio), 900))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4:
            points = approx.reshape(4, 2).astype("float32") * ratio
            return order_points(points.tolist())

    height, width = image.shape[:2]
    margin_x = width * 0.05
    margin_y = height * 0.05
    return order_points([
        (margin_x, margin_y),
        (width - margin_x, margin_y),
        (width - margin_x, height - margin_y),
        (margin_x, height - margin_y),
    ])


def warp_document_image(
    source_path: Path,
    target_path: Path,
    paper_mm: tuple[float, float],
    dpi: int,
    corners=None,
) -> dict:
    import cv2
    import numpy as np

    image = cv2.imread(str(source_path))
    if image is None:
        raise ValueError(f"Cannot read image: {source_path}")

    src = np.array(order_points(corners or find_document_corners(source_path)), dtype="float32")
    width_px, height_px = paper_pixel_size(paper_mm, dpi)
    dst = np.array(
        [[0, 0], [width_px - 1, 0], [width_px - 1, height_px - 1], [0, height_px - 1]],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(image, transform, (width_px, height_px), flags=cv2.INTER_CUBIC)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target_path), warped)
    return {"mode": "document", "document_corners_px": src.round(2).tolist(), "output_pixel_size": [width_px, height_px]}


def prepare_clean_pages(
    raw_pages: list[Path],
    out_dir: Path,
    preprocess: str,
    paper_mm: tuple[float, float],
    dpi: int,
    document_corners: str | None,
) -> tuple[list[Path], list[dict]]:
    from PIL import Image

    clean_dir = ensure_dirs(out_dir)["clean"]
    clean_pages: list[Path] = []
    preprocess_info: list[dict] = []
    manual_corners = parse_document_corners(document_corners)

    for index, raw_path in enumerate(raw_pages, start=1):
        target = clean_dir / f"page-{index:03d}.png"
        if preprocess == "document":
            info = warp_document_image(raw_path, target, paper_mm, dpi, manual_corners)
        else:
            with Image.open(raw_path) as image:
                image.convert("RGB").save(target)
            info = {"mode": "none"}
        clean_pages.append(target)
        preprocess_info.append(info)

    return clean_pages, preprocess_info


def draw_grid_overlay(
    image_path: Path,
    out_path: Path,
    grid_size: int,
    major_step: int,
    minor_step: int,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(image_path).convert("RGB") as image:
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        font = ImageFont.load_default()

        for step, color, label in (
            (minor_step, (30, 120, 255, 70), False),
            (major_step, (255, 40, 40, 130), True),
        ):
            for coord in range(0, grid_size + 1, step):
                x = round(coord / grid_size * width)
                y = round(coord / grid_size * height)
                draw.line([(x, 0), (x, height)], fill=color, width=1)
                draw.line([(0, y), (width, y)], fill=color, width=1)
                if label and coord not in (0, grid_size):
                    draw.text((x + 3, 3), str(coord), fill=(180, 0, 0, 220), font=font)
                    draw.text((3, y + 3), str(coord), fill=(180, 0, 0, 220), font=font)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path)


def detect_pdf_text(source: Path, rendered_sizes: list[tuple[int, int]], paper_mm: tuple[float, float]) -> list[list[Element]]:
    if source.suffix.lower() != ".pdf":
        return [[] for _ in rendered_sizes]

    import fitz

    pages: list[list[Element]] = []
    doc = fitz.open(source)
    for page_index, page in enumerate(doc):
        width_px, height_px = rendered_sizes[page_index]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        elements: list[Element] = []
        for block_index, block in enumerate(page.get_text("blocks"), start=1):
            x1, y1, x2, y2, text, *_ = block
            clean_text = " ".join(str(text).split())
            if not clean_text:
                continue
            bbox_px = [
                round(x1 / page_width * width_px),
                round(y1 / page_height * height_px),
                round(x2 / page_width * width_px),
                round(y2 / page_height * height_px),
            ]
            bbox_norm = pixel_bbox_to_norm(bbox_px, width_px, height_px)
            elements.append(
                Element(
                    id=f"p{page_index + 1}_text_{block_index:03d}",
                    type="text",
                    bbox_px=bbox_px,
                    bbox_norm=bbox_norm,
                    bbox_mm=norm_bbox_to_mm(bbox_norm, paper_mm),
                    confidence=1.0,
                    text=clean_text,
                )
            )
        pages.append(elements)
    return pages


def detect_shapes_with_cv(image_path: Path, page_num: int, paper_mm: tuple[float, float]) -> list[Element]:
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        return []
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = max(100, int(width * height * 0.0002))

    elements: list[Element] = []
    for index, contour in enumerate(contours, start=1):
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < min_area or w < 8 or h < 8:
            continue
        bbox_px = [int(x), int(y), int(x + w), int(y + h)]
        bbox_norm = pixel_bbox_to_norm(bbox_px, width, height)
        elements.append(
            Element(
                id=f"p{page_num}_box_{index:03d}",
                type="box",
                bbox_px=bbox_px,
                bbox_norm=bbox_norm,
                bbox_mm=norm_bbox_to_mm(bbox_norm, paper_mm),
                confidence=0.75,
            )
        )
    return elements

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

def merge_float_positions(positions: list[float], tolerance: float) -> list[float]:
    if not positions:
        return []
    sorted_positions = sorted(positions)
    merged = []
    cluster = [sorted_positions[0]]
    for position in sorted_positions[1:]:
        if position - cluster[-1] <= tolerance:
            cluster.append(position)
        else:
            merged.append(round(sum(cluster) / len(cluster), 2))
            cluster = [position]
    merged.append(round(sum(cluster) / len(cluster), 2))
    return merged

def line_covers_interval(segments: list[tuple[float, float, float]], line_pos: float, start: float, end: float, tolerance: float) -> bool:
    for pos, seg_start, seg_end in segments:
        if abs(pos - line_pos) <= tolerance and seg_start <= start + tolerance and seg_end >= end - tolerance:
            return True
    return False

def detect_structural_grid(image_path: Path, paper_mm: tuple[float, float]) -> dict:
    import cv2
    import numpy as np

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return {
            "vertical_lines_mm": [],
            "horizontal_lines_mm": [],
            "cells_mm": [],
            "cells_css": [],
        }

    height, width = image.shape[:2]
    blur = cv2.GaussianBlur(image, (3, 3), 0)
    _threshold, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 60), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 60)))
    horizontal_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)

    horizontal_contours, _ = cv2.findContours(horizontal_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    vertical_contours, _ = cv2.findContours(vertical_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_h_len_px = max(30, width * 0.04)
    min_v_len_px = max(24, height * 0.025)
    edge_margin_x_mm = paper_mm[0] * 0.02
    edge_margin_y_mm = paper_mm[1] * 0.015
    horizontal_segments: list[tuple[float, float, float]] = []
    vertical_segments: list[tuple[float, float, float]] = []

    for contour in horizontal_contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < min_h_len_px:
            continue
        y_mm = round((y + h / 2) / height * paper_mm[1], 2)
        x1_mm = round(x / width * paper_mm[0], 2)
        x2_mm = round((x + w) / width * paper_mm[0], 2)
        if y_mm <= edge_margin_y_mm or y_mm >= paper_mm[1] - edge_margin_y_mm:
            continue
        horizontal_segments.append((y_mm, x1_mm, x2_mm))

    for contour in vertical_contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < min_v_len_px:
            continue
        x_mm = round((x + w / 2) / width * paper_mm[0], 2)
        y1_mm = round(y / height * paper_mm[1], 2)
        y2_mm = round((y + h) / height * paper_mm[1], 2)
        if x_mm <= edge_margin_x_mm or x_mm >= paper_mm[0] - edge_margin_x_mm:
            continue
        vertical_segments.append((x_mm, y1_mm, y2_mm))

    merge_tolerance_mm = 2.6
    cover_tolerance_mm = 1.8
    vertical_lines_mm = merge_float_positions([segment[0] for segment in vertical_segments], merge_tolerance_mm)
    horizontal_lines_mm = merge_float_positions([segment[0] for segment in horizontal_segments], merge_tolerance_mm)
    cells_mm = []
    cells_css = []
    min_cell_w_mm = paper_mm[0] * 0.015
    min_cell_h_mm = paper_mm[1] * 0.008

    for x1, x2 in zip(vertical_lines_mm, vertical_lines_mm[1:]):
        for y1, y2 in zip(horizontal_lines_mm, horizontal_lines_mm[1:]):
            if x2 - x1 < min_cell_w_mm or y2 - y1 < min_cell_h_mm:
                continue
            has_top = line_covers_interval(horizontal_segments, y1, x1, x2, cover_tolerance_mm)
            has_bottom = line_covers_interval(horizontal_segments, y2, x1, x2, cover_tolerance_mm)
            has_left = line_covers_interval(vertical_segments, x1, y1, y2, cover_tolerance_mm)
            has_right = line_covers_interval(vertical_segments, x2, y1, y2, cover_tolerance_mm)
            if not (has_top and has_bottom and has_left and has_right):
                continue
            cell = [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]
            cells_mm.append(cell)
            cells_css.append({"bbox_mm": cell, "css": bbox_to_css_mm(cell, {"border": "1px solid #111"})})

    return {
        "vertical_lines_mm": vertical_lines_mm,
        "horizontal_lines_mm": horizontal_lines_mm,
        "column_widths_mm": [round(x2 - x1, 2) for x1, x2 in zip(vertical_lines_mm, vertical_lines_mm[1:])],
        "row_heights_mm": [round(y2 - y1, 2) for y1, y2 in zip(horizontal_lines_mm, horizontal_lines_mm[1:])],
        "horizontal_segments_mm": [[pos, start, end] for pos, start, end in sorted(horizontal_segments)],
        "vertical_segments_mm": [[pos, start, end] for pos, start, end in sorted(vertical_segments)],
        "cells_mm": cells_mm,
        "cells_css": cells_css,
    }


def detect_ocr_text(image_path: Path, page_num: int, paper_mm: tuple[float, float]) -> list[Element]:
    from PIL import Image
    import pytesseract

    with Image.open(image_path) as image:
        width, height = image.size
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    elements: list[Element] = []
    for index, text in enumerate(data.get("text", []), start=1):
        clean_text = " ".join(str(text).split())
        if not clean_text:
            continue
        try:
            confidence = float(data["conf"][index - 1])
        except (TypeError, ValueError):
            confidence = -1
        if confidence < 0:
            continue
        x = int(data["left"][index - 1])
        y = int(data["top"][index - 1])
        w = int(data["width"][index - 1])
        h = int(data["height"][index - 1])
        bbox_px = [x, y, x + w, y + h]
        bbox_norm = pixel_bbox_to_norm(bbox_px, width, height)
        elements.append(
            Element(
                id=f"p{page_num}_ocr_{index:03d}",
                type="text",
                bbox_px=bbox_px,
                bbox_norm=bbox_norm,
                bbox_mm=norm_bbox_to_mm(bbox_norm, paper_mm),
                confidence=round(confidence / 100, 2),
                text=clean_text,
            )
        )
    return elements


STATIC_TEXT_HINTS = {
    "delivery to",
    "warehouse",
    "sj date",
    "sj no",
    "do date",
    "do no",
    "so date",
    "so no",
    "no mobil",
    "khs date",
    "kontrak rinci date",
    "no kr",
    "no khs",
    "no",
    "code",
    "description",
    "unit",
    "qty",
    "length",
    "weight",
    "netto",
    "bruto",
    "no drum",
    "receiver",
    "driver",
    "approval",
    "total",
}

FIELD_HINTS = {
    "delivery to": ("partner_id", "recipient/customer name and address"),
    "warehouse": ("warehouse_id", "warehouse name and address"),
    "sj date": ("date", "surat jalan date"),
    "sj no": ("name", "surat jalan number"),
    "do date": ("do_date", "delivery order date"),
    "do no": ("origin", "delivery order/reference number"),
    "so date": ("sale_order_date", "sale order date"),
    "so no": ("sale_id.name", "sale order number"),
    "no mobil": ("vehicle_no", "vehicle/license number"),
    "khs date": ("khs_date", "KHS date"),
    "kontrak rinci date": ("contract_detail_date", "contract detail date"),
    "no kr": ("contract_detail_no", "contract detail number"),
    "no khs": ("khs_no", "KHS number"),
    "code": ("line.product_id.default_code", "line item product code"),
    "description": ("line.name", "line item description"),
    "unit": ("line.product_uom.name", "line item unit"),
    "qty": ("line.quantity", "line item quantity"),
    "length": ("line.length", "line item length"),
    "netto": ("line.weight_net", "line item net weight"),
    "bruto": ("line.weight_gross", "line item gross weight"),
    "no drum": ("line.lot_id.name", "line item drum/serial number"),
    "receiver": ("receiver_name", "receiver signature label/name"),
    "driver": ("driver_name", "driver signature label/name"),
    "approval": ("approver_id.name", "approval signature label/name"),
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalized_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", normalize_text(text).lower()).strip()


def classify_text_role(text: str) -> str:
    key = normalized_key(text)
    if not key:
        return "unknown"
    if key in STATIC_TEXT_HINTS:
        return "static_text"
    if "surat jalan" in key:
        return "static_text"
    if re.fullmatch(r"\d+([.,]\d+)*", key):
        return "dynamic_value"
    if re.search(r"\d{1,2} ?[a-z]{3} ?\d{2,4}", key):
        return "dynamic_value"
    if re.search(r"\b[a-z]{1,4}[-/]?\d{2,}", key):
        return "dynamic_value"
    if any(token in key for token in ("pt ", "persero", "smp", "pln", "jalan", "telp", "pic")):
        return "dynamic_value"
    return "candidate_value"


def suggest_odoo_field(text: str) -> dict:
    key = normalized_key(text)
    for hint, (field, meaning) in FIELD_HINTS.items():
        if hint in key or key in hint:
            return {"field": field, "meaning": meaning, "confidence": 0.75}
    role = classify_text_role(text)
    if role == "static_text":
        return {"field": None, "meaning": "static printed label", "confidence": 0.6}
    return {"field": "review_required", "meaning": "dynamic value; map to Odoo field manually", "confidence": 0.3}


def infer_report_kind(elements: list[Element]) -> str:
    joined = " ".join(normalized_key(element.text or "") for element in elements if element.text)
    if "surat jalan" in joined or "delivery to" in joined or "no drum" in joined:
        return "delivery_order"
    if "invoice" in joined:
        return "invoice"
    if "purchase order" in joined or "po no" in joined:
        return "purchase_order"
    return "custom_report"


def identify_line_region(elements: list[Element]) -> dict:
    text_elements = [element for element in elements if element.type == "text" and element.text]
    header = None
    for element in text_elements:
        key = normalized_key(element.text or "")
        if ("description" in key and "qty" in key) or ("code" in key and "description" in key):
            header = element
            break
    footer_candidates = [
        element for element in text_elements
        if any(token in normalized_key(element.text or "") for token in ("receiver", "driver", "approval", "total"))
    ]
    if header:
        header_bottom = header.bbox_norm[3]
        footer_top = min([element.bbox_norm[1] for element in footer_candidates if element.bbox_norm[1] > header_bottom] or [900])
        sample_rows = [
            element for element in text_elements
            if header_bottom <= element.bbox_norm[1] <= footer_top and re.match(r"^\s*\d+\b", element.text or "")
        ]
        row_height = None
        if len(sample_rows) >= 2:
            ys = sorted(element.bbox_norm[1] for element in sample_rows)
            deltas = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0]
            row_height = round(sum(deltas) / len(deltas), 2) if deltas else None
        return {
            "header_element_id": header.id,
            "bbox_norm": [header.bbox_norm[0], header_bottom, header.bbox_norm[2], footer_top],
            "row_height_norm": row_height,
            "detected_sample_rows": len(sample_rows),
        }
    return {
        "header_element_id": None,
        "bbox_norm": None,
        "row_height_norm": None,
        "detected_sample_rows": 0,
    }


def bbox_width_mm(bbox_mm: Iterable[float]) -> float:
    x1, _y1, x2, _y2 = bbox_mm
    return round(float(x2) - float(x1), 2)


def bbox_height_mm(bbox_mm: Iterable[float]) -> float:
    _x1, y1, _x2, y2 = bbox_mm
    return round(float(y2) - float(y1), 2)


def bbox_to_css_mm(bbox_mm: Iterable[float], extra: dict[str, str] | None = None) -> str:
    x1, y1, x2, y2 = [float(value) for value in bbox_mm]
    declarations = {
        "position": "absolute",
        "left": f"{round(x1, 2):g}mm",
        "top": f"{round(y1, 2):g}mm",
        "width": f"{round(x2 - x1, 2):g}mm",
        "height": f"{round(y2 - y1, 2):g}mm",
        "box-sizing": "border-box",
    }
    if extra:
        declarations.update(extra)
    return "; ".join(f"{name}: {value}" for name, value in declarations.items()) + ";"


def text_fit_constraints(element: Element) -> dict:
    height = max(1.0, bbox_height_mm(element.bbox_mm))
    text = normalize_text(element.text or "")
    font_size = min(5.0, max(2.0, round(height * 0.68, 2)))
    if "surat jalan" in normalized_key(text):
        font_size = min(5.8, max(font_size, 4.8))
    return {
        "nowrap": True,
        "overflow": "hidden",
        "text_overflow": "clip",
        "white_space": "nowrap",
        "line_height_mm": round(font_size * 1.12, 2),
        "font_size_mm": font_size,
        "fit_strategy": "single_line_shrink_to_bbox",
    }


def mm_bbox_from_norm(bbox_norm: list[int] | None, paper_mm: tuple[float, float]) -> list[float] | None:
    if not bbox_norm:
        return None
    return norm_bbox_to_mm(bbox_norm, paper_mm)


def infer_signature_regions(elements: list[Element], paper_mm: tuple[float, float]) -> list[dict]:
    signature_tokens = ("receiver", "driver", "warehouse", "approval")
    text_matches = [
        element for element in elements
        if element.type == "text" and element.text and any(token in normalized_key(element.text) for token in signature_tokens)
    ]
    if text_matches:
        return [
            {
                "source": "text",
                "element_id": element.id,
                "label": normalize_text(element.text or ""),
                "bbox_norm": element.bbox_norm,
                "bbox_mm": element.bbox_mm,
                "css": bbox_to_css_mm(element.bbox_mm),
            }
            for element in sorted(text_matches, key=lambda item: (item.bbox_norm[1], item.bbox_norm[0]))
        ]

    bottom_boxes = [
        element for element in elements
        if element.type == "box"
        and element.bbox_norm[1] >= 760
        and bbox_width_mm(element.bbox_mm) >= paper_mm[0] * 0.08
        and bbox_height_mm(element.bbox_mm) >= paper_mm[1] * 0.03
    ]
    return [
        {
            "source": "box",
            "element_id": element.id,
            "label": None,
            "bbox_norm": element.bbox_norm,
            "bbox_mm": element.bbox_mm,
            "css": bbox_to_css_mm(element.bbox_mm, {"border": "1px solid #111"}),
        }
        for element in sorted(bottom_boxes, key=lambda item: (item.bbox_norm[1], item.bbox_norm[0]))
    ]


def build_qweb_layout_spec(pages: list[PageAnalysis], blueprint: dict) -> dict:
    page_specs = []
    for page in pages:
        static_boxes = []
        static_text = []
        value_fields = []
        elements = sorted(page.elements, key=lambda element: (element.bbox_norm[1], element.bbox_norm[0], element.id))

        for element in elements:
            if element.type in {"box", "image"}:
                static_boxes.append(
                    {
                        "id": element.id,
                        "type": element.type,
                        "bbox_norm": element.bbox_norm,
                        "bbox_mm": element.bbox_mm,
                        "css": bbox_to_css_mm(element.bbox_mm, {"border": "1px solid #111"}),
                    }
                )
                continue

            if element.type != "text" or not element.text:
                continue

            role = classify_text_role(element.text)
            constraints = text_fit_constraints(element)
            text_spec = {
                "id": element.id,
                "text": element.text,
                "role": role,
                "bbox_norm": element.bbox_norm,
                "bbox_mm": element.bbox_mm,
                "css": bbox_to_css_mm(
                    element.bbox_mm,
                    {
                        "white-space": constraints["white_space"],
                        "overflow": constraints["overflow"],
                        "font-size": f"{constraints['font_size_mm']:g}mm",
                        "line-height": f"{constraints['line_height_mm']:g}mm",
                    },
                ),
                "fit": constraints,
                "odoo_field_suggestion": suggest_odoo_field(element.text),
            }
            if role == "static_text":
                static_text.append(text_spec)
            else:
                value_fields.append(text_spec)

        line_region = blueprint["regions"]["line_items"]
        line_bbox_mm = mm_bbox_from_norm(line_region.get("bbox_norm"), page.paper_mm)
        page_specs.append(
            {
                "page": page.page,
                "paper_mm": list(page.paper_mm),
                "page_css": bbox_to_css_mm([0, 0, page.paper_mm[0], page.paper_mm[1]], {"overflow": "hidden"}),
                "static_boxes": static_boxes,
                "static_text": static_text,
                "value_fields": value_fields,
                "line_table": {
                    "bbox_norm": line_region.get("bbox_norm"),
                    "bbox_mm": line_bbox_mm,
                    "css": bbox_to_css_mm(line_bbox_mm) if line_bbox_mm else None,
                    "header_element_id": line_region.get("header_element_id"),
                    "row_height_norm": line_region.get("row_height_norm"),
                    "pagination": blueprint["pagination"]["line_items"],
                },
                "signature_boxes": infer_signature_regions(elements, page.paper_mm),
                "structural_grid": page.structural_grid or {
                    "vertical_lines_mm": [],
                    "horizontal_lines_mm": [],
                    "column_widths_mm": [],
                    "row_heights_mm": [],
                    "horizontal_segments_mm": [],
                    "vertical_segments_mm": [],
                    "cells_mm": [],
                    "cells_css": [],
                },
            }
        )

    return {
        "renderer": "odoo-qweb-pdf",
        "units": "mm",
        "coordinate_source": "bbox_mm",
        "rules": [
            "Use absolute-positioned elements inside the page container.",
            "Use these CSS declarations directly before making visual tweaks.",
            "Keep heading and fixed labels on one line with white-space: nowrap and overflow: hidden.",
            "Render the PDF back to PNG and compare against clean/page-*.png before accepting the layout.",
        ],
        "pages": page_specs,
    }


def build_report_blueprint(pages: list[PageAnalysis], max_lines_per_page: int | None = None) -> dict:
    elements = [element for page in pages for element in page.elements]
    text_roles = {}
    static_assets = []
    value_candidates = []

    for element in elements:
        if element.type == "text" and element.text:
            role = classify_text_role(element.text)
            suggestion = suggest_odoo_field(element.text)
            text_roles[element.id] = {
                "text": element.text,
                "role": role,
                "bbox_norm": element.bbox_norm,
                "bbox_mm": element.bbox_mm,
                "odoo_field_suggestion": suggestion,
            }
            if role in {"dynamic_value", "candidate_value"}:
                value_candidates.append(element.id)
        elif element.type in {"box", "image"}:
            static_assets.append({"element_id": element.id, "type": element.type, "bbox_norm": element.bbox_norm, "bbox_mm": element.bbox_mm})

    line_region = identify_line_region(elements)
    computed_max = max_lines_per_page
    if computed_max is None and line_region["bbox_norm"] and line_region["row_height_norm"]:
        y1, y2 = line_region["bbox_norm"][1], line_region["bbox_norm"][3]
        computed_max = max(1, int((y2 - y1) // line_region["row_height_norm"]))

    return {
        "report_kind": infer_report_kind(elements),
        "text_roles": text_roles,
        "value_candidates": value_candidates,
        "static_assets": static_assets,
        "regions": {
            "line_items": line_region,
        },
        "pagination": {
            "line_items": {
                "strategy": "repeat_header_and_continue_lines",
                "max_lines_first_page": computed_max,
                "max_lines_next_pages": computed_max,
                "overflow_behavior": "render_remaining_lines_on_following_pages",
            }
        },
        "odoo_notes": [
            "Treat field suggestions as draft mappings; verify against the actual Odoo model.",
            "Static labels and borders should be implemented as QWeb text/CSS or static background assets.",
            "Line items should be rendered with t-foreach and split into pages using the max lines values.",
        ],
    }


def analyze_pages(source: Path, out_dir: Path, args: argparse.Namespace) -> list[PageAnalysis]:
    from PIL import Image

    paper_mm = resolve_paper_mm(args.paper, args.paper_width_mm, args.paper_height_mm)
    raw_pages = render_source_pages(source, out_dir, args.dpi)
    page_images, preprocess_info = prepare_clean_pages(
        raw_pages,
        out_dir,
        args.preprocess,
        paper_mm,
        args.dpi,
        args.document_corners,
    )
    rendered_sizes: list[tuple[int, int]] = []
    for image_path in page_images:
        with Image.open(image_path) as image:
            rendered_sizes.append(image.size)

    pdf_text_pages = [[] for _ in rendered_sizes] if args.preprocess == "document" else detect_pdf_text(source, rendered_sizes, paper_mm)
    analyses: list[PageAnalysis] = []
    annotated_dir = ensure_dirs(out_dir)["annotated"]

    for index, image_path in enumerate(page_images, start=1):
        width, height = rendered_sizes[index - 1]
        overlay_path = annotated_dir / f"page-{index:03d}-grid.png"
        draw_grid_overlay(image_path, overlay_path, args.grid_size, args.major_step, args.minor_step)

        elements = list(pdf_text_pages[index - 1])
        if args.detect == "full":
            elements.extend(detect_shapes_with_cv(image_path, index, paper_mm))
            ocr_available = has_module("pytesseract") and has_executable("tesseract")
            if ocr_available and (not elements or source.suffix.lower() != ".pdf"):
                elements.extend(detect_ocr_text(image_path, index, paper_mm))

        structural_grid = detect_structural_grid(image_path, paper_mm) if args.detect == "full" else None
        analyses.append(PageAnalysis(index, (width, height), paper_mm, elements, preprocess_info[index - 1], structural_grid))

    return analyses


def write_manifest(
    out_dir: Path,
    source: Path,
    target: str,
    pages: list[PageAnalysis],
    paper: str | None = None,
    blueprint: dict | None = None,
) -> Path:
    blueprint = blueprint or build_report_blueprint(pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": str(source),
        "target": target,
        "coordinate_system": COORDINATE_SYSTEM,
        "paper": paper,
        "pages": [page.to_dict() for page in pages],
        "report_blueprint": blueprint,
        "qweb_layout_spec": build_qweb_layout_spec(pages, blueprint),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def write_prompt(out_dir: Path, source: Path, target: str, pages: list[PageAnalysis], blueprint: dict | None = None) -> Path:
    blueprint = blueprint or build_report_blueprint(pages)
    layout_spec = build_qweb_layout_spec(pages, blueprint)
    prompt_path = out_dir / "prompt.md"
    lines = [
        f"# Layout Prompt for {source.name}",
        "",
        f"Use `manifest.json` as the coordinate source of truth. Coordinates are `{COORDINATE_SYSTEM}`.",
        "Use `clean/page-*.png` as the scanner-like document image. Use `raw/page-*.png` only to audit photo preprocessing.",
        "For Odoo QWeb PDF, place elements using `bbox_mm`: left=x1, top=y1, width=x2-x1, height=y2-y1.",
        "Use `report_blueprint` in `manifest.json` to separate static labels/assets from dynamic Odoo values.",
        "Use `qweb_layout_spec` in `manifest.json` for CSS-ready absolute positions, text-fit constraints, line table regions, and signature boxes.",
        "Use `qweb_layout_spec.pages[].structural_grid` for measured box/table line coordinates and cell widths.",
        "",
        "Reference files:",
    ]
    for page in pages:
        lines.append(f"- Page {page.page}: `raw/page-{page.page:03d}.png`, `clean/page-{page.page:03d}.png`, `annotated/page-{page.page:03d}-grid.png`")
    lines.extend(
        [
            "",
            "QWeb page container:",
            "",
            "```html",
            f'<div class="page" style="position: relative; width: {pages[0].paper_mm[0]}mm; height: {pages[0].paper_mm[1]}mm;">',
            "  <!-- absolutely positioned report elements -->",
            "</div>",
            "```",
            "",
            f"Target renderer: `{target}`.",
            f"Report kind suggestion: `{blueprint['report_kind']}`.",
            f"Line pagination strategy: `{blueprint['pagination']['line_items']['strategy']}`.",
            f"QWeb layout spec pages: `{len(layout_spec['pages'])}`.",
            f"Structural grid cells on page 1: `{len(layout_spec['pages'][0]['structural_grid']['cells_mm'])}`.",
            "",
            "After rendering the Odoo PDF to PNG, compare it against the rectified reference:",
            "",
            "```bash",
            "python3 scripts/compare_report_render.py \\",
            "  --reference <analysis-out>/clean/page-001.png \\",
            "  --rendered <generated-report-page-001.png> \\",
            "  --out <analysis-out>/compare \\",
            "  --mode edge",
            "```",
            "",
            "For focused box/table/footer checks, use structure mode with a paper-mm crop, for example `--mode structure --crop-mm \"15,40,205,135\"`.",
            "",
            "Inspect `compare_metrics.json` and `diff-overlay.png`; fix coordinates in millimeters and render again.",
        ]
    )
    prompt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return prompt_path


def write_qweb_layout_spec(out_dir: Path, pages: list[PageAnalysis], blueprint: dict) -> Path:
    spec = build_qweb_layout_spec(pages, blueprint)
    spec_path = out_dir / "qweb_layout_spec.md"
    lines = [
        "# QWeb Layout Spec",
        "",
        "Use these values as the first-pass QWeb coordinates. Do not estimate from the grid by eye when a bbox exists.",
        "",
    ]
    for page in spec["pages"]:
        lines.extend(
            [
                f"## Page {page['page']}",
                "",
                f"- Paper: `{page['paper_mm'][0]}mm x {page['paper_mm'][1]}mm`",
                f"- Page CSS: `{page['page_css']}`",
                f"- Static boxes: `{len(page['static_boxes'])}`",
                f"- Static text: `{len(page['static_text'])}`",
                f"- Dynamic/candidate values: `{len(page['value_fields'])}`",
                f"- Signature boxes: `{len(page['signature_boxes'])}`",
                f"- Structural grid cells: `{len(page['structural_grid']['cells_mm'])}`",
                "",
            ]
        )
        line_table = page["line_table"]
        if line_table["bbox_mm"]:
            lines.append(f"- Line table CSS: `{line_table['css']}`")
            lines.append("")
    spec_path.write_text("\n".join(lines), encoding="utf-8")
    return spec_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", default="odoo-qweb", choices=["odoo-qweb", "html-css", "reportlab"])
    parser.add_argument("--detect", default="full", choices=["grid", "full"])
    parser.add_argument("--preprocess", default="none", choices=["none", "document"])
    parser.add_argument("--document-corners", help="Manual source corners as x,y;x,y;x,y;x,y when auto crop is wrong")
    parser.add_argument("--grid-size", type=int, default=1000)
    parser.add_argument("--major-step", type=int, default=100)
    parser.add_argument("--minor-step", type=int, default=25)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--paper", default="a4", choices=sorted([*PAPER_SIZES_MM, "custom"]))
    parser.add_argument("--paper-width-mm", type=float)
    parser.add_argument("--paper-height-mm", type=float)
    parser.add_argument("--max-lines-per-page", type=int, help="Override detected line-item capacity for pagination hints")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.source.exists():
        print(f"Source not found: {args.source}", file=sys.stderr)
        return 2

    try:
        resolve_paper_mm(args.paper, args.paper_width_mm, args.paper_height_mm)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    messages = missing_dependency_messages(args.detect, args.preprocess)
    if messages:
        print("Missing dependencies. Install:", file=sys.stderr)
        for message in messages:
            print(f"  {message}", file=sys.stderr)
        return 2

    pages = analyze_pages(args.source, args.out, args)
    blueprint = build_report_blueprint(pages, args.max_lines_per_page)
    write_manifest(args.out, args.source, args.target, pages, args.paper, blueprint)
    write_prompt(args.out, args.source, args.target, pages, blueprint)
    write_qweb_layout_spec(args.out, pages, blueprint)
    print(f"Wrote layout analysis to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
