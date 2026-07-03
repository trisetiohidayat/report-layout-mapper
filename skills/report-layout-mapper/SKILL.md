---
name: report-layout-mapper
description: Use when Codex needs to reproduce an Odoo report layout from a PDF or image/photo template, especially when camera angle correction, document crop/deskew, paper size, static-vs-dynamic text classification, Odoo field mapping suggestions, line-item pagination, absolute positioning, boxes, tables, scanned reports, QWeb PDFs, grid overlays, or normalized layout coordinates are needed.
---

# Report Layout Mapper

## Overview

Use this skill before building pixel-sensitive reports from a visual reference. Convert camera photos into scanner-like document images first, then apply the grid and coordinate manifest to the rectified paper area.

For photo inputs, do not put the grid on the whole photo. Crop/deskew the document first.

## Quick Start

Run the analyzer from this skill directory:

```bash
python3 scripts/analyze_report_layout.py <source.pdf|source.png|source.jpg> \
  --out <output-dir> \
  --target odoo-qweb \
  --preprocess document \
  --detect full \
  --grid-size 1000 \
  --major-step 100 \
  --minor-step 25 \
  --dpi 220 \
  --paper a4 \
  --max-lines-per-page 10
```

Outputs:

- `raw/page-001.png`: original rendered photo/PDF page.
- `clean/page-001.png`: scanner-like document image after crop/deskew, or rendered source when preprocessing is disabled.
- `annotated/page-001-grid.png`: rendered page with normalized grid overlay.
- `manifest.json`: source of truth for coordinates.
- `prompt.md`: compact instructions for the report-building agent.
- `qweb_layout_spec.md`: compact summary of the QWeb placement contract.

After rendering the Odoo PDF to PNG, run the comparator:

```bash
python3 scripts/compare_report_render.py \
  --reference <output-dir>/clean/page-001.png \
  --rendered <rendered-report-page-001.png> \
  --out <compare-output-dir> \
  --mode edge
```

Use `--mode structure` for box/table/signature layout fidelity. This ignores most text/value differences and compares long horizontal/vertical lines:

```bash
python3 scripts/compare_report_render.py \
  --reference <output-dir>/clean/page-001.png \
  --rendered <rendered-report-page-001.png> \
  --out <compare-output-dir>/header-structure \
  --mode structure \
  --crop-mm "15,40,205,135" \
  --min-line-length-mm 6 \
  --line-merge-tolerance-mm 0.7
```

Use `--mode edge` when validating title text, static labels, and signature names. Use `--mode structure` when validating box widths, table columns, footer boxes, and signature box positions.

Comparator outputs:

- `compare_metrics.json`: similarity, mismatch ratio, mismatch bounding box.
- `structure_line_deltas_mm`: in `--mode structure`, matched/missing/extra vertical and horizontal lines in millimeters, including `delta_mm`, `max_abs_delta_mm`, `mean_abs_delta_mm`, `min_line_length_mm`, and `line_merge_tolerance_mm`.
- `diff-mask.png`: binary mismatch mask.
- `diff-overlay.png`: rendered page with mismatch areas highlighted.

Use `structure_line_deltas_mm` before moving QWeb boxes by eye. If a region has a consistent positive `delta_mm`, the rendered line is too far right/down; reduce the corresponding CSS `left` or `top` by that amount. If the delta is negative, increase the CSS coordinate. Missing/extra lines usually mean a border is absent, too short for detection, or a rendered element is scaled by paperformat/wkhtmltopdf settings. If a thick border creates duplicate nearby lines, increase `--line-merge-tolerance-mm` slightly instead of treating the duplicate as a real layout line.

`manifest.json` also includes `report_blueprint`, a draft interpretation of the report:

- `text_roles`: detected text classified as `static_text`, `dynamic_value`, or `candidate_value`.
- `value_candidates`: text elements likely sourced from Odoo fields.
- `static_assets`: boxes/images/borders likely implemented as static CSS or static image assets.
- `odoo_field_suggestion`: draft field names/meanings for dynamic values.
- `regions.line_items`: likely table body region.
- `pagination.line_items`: max line hints and overflow behavior.

`manifest.json` also includes `qweb_layout_spec`, the CSS-ready layout contract:

- `page_css`: physical page container in millimeters.
- `static_boxes`: boxes/images/borders with absolute `bbox_mm` and CSS.
- `static_text`: fixed labels/headings with text-fit rules such as `white-space: nowrap`.
- `value_fields`: candidate dynamic values with Odoo field suggestions.
- `line_table`: table region and pagination hints.
- `signature_boxes`: detected footer/signature regions.
- `structural_grid`: merged horizontal/vertical line coordinates and cell boxes in millimeters.

Use `qweb_layout_spec` before reading the grid visually. The grid is for inspection; the spec is the first-pass implementation source.

For box widths, table columns, and signature boxes, prefer `qweb_layout_spec.pages[].structural_grid` over raw contour boxes. Use:

- `vertical_lines_mm` and `horizontal_lines_mm` for measured line positions.
- `column_widths_mm` and `row_heights_mm` for repeated table/header dimensions.
- `cells_mm` and `cells_css` for closed boxes detected from all four sides.

Structural grid data is merged from long horizontal/vertical lines and is less noisy than OCR or hand-read grid coordinates. If `cells_mm` is sparse because a scan has broken lines, still use `vertical_lines_mm`, `horizontal_lines_mm`, and widths/heights to build the QWeb CSS.

Do not accept a layout based on visual impression only. Compare the rendered PNG against the rectified reference with `scripts/compare_report_render.py`, then inspect `diff-overlay.png` for title, boxes, table columns, totals, and signature names.

Use region crops and structure mode for acceptance because full-page scans include paper edges, lighting, handwritten notes, and sample values that are not part of the reusable Odoo layout.

## Paper Size Gate

Before analyzing a photo or scan, lock the physical paper size. Ask the user if it is not explicit.

Supported presets:

| Paper | Size mm |
|---|---:|
| A4 | `210 x 297` |
| A5 | `148 x 210` |
| Legal | `215.9 x 355.6` |
| Letter | `215.9 x 279.4` |

For any other paper, use:

```bash
--paper custom --paper-width-mm <width> --paper-height-mm <height>
```

If the user only says "custom", ask for exact width and height in millimeters. Do not guess; wrong paper size makes every `bbox_mm` wrong.

## Coordinate Rules

- Use `manifest.json` as the source of truth.
- Coordinate system: `normalized_0_1000_top_left`.
- Origin: top-left.
- Bounding box: `[x1, y1, x2, y2]`.
- Prefer `bbox_mm` for Odoo QWeb PDF placement.
- Use annotated grid images only for visual checking and prompt context.
- Do not use raw pixels for final QWeb layout unless the renderer is pixel-based.

## Detection Modes

| Mode | Use for | Notes |
|---|---|---|
| `--detect grid` | Fast coordinate overlay only | Needs PyMuPDF/Pillow. No OCR or shape detection. |
| `--detect full` | Templates with text, boxes, lines, or scanned pages | Adds OpenCV box detection. Tesseract OCR is used only when installed. |

If required detection dependencies are missing, the script prints exact install commands. Missing OCR does not block box/layout detection. Local-only detection is the default; do not send report templates to cloud vision APIs unless the user explicitly approves.

## Preprocessing Modes

| Mode | Use for | Notes |
|---|---|---|
| `--preprocess none` | Clean PDF exports or already-scanned images | Grid is applied directly to the rendered page. |
| `--preprocess document` | Camera photos at any angle | Finds paper corners, perspective-warps to selected paper size, then applies grid. |

If auto corner detection is wrong, rerun with manual source image corners:

```bash
--document-corners "x1,y1;x2,y2;x3,y3;x4,y4"
```

Corner order can be any order; the script normalizes it to top-left, top-right, bottom-right, bottom-left.

## Odoo QWeb

For Odoo QWeb report generation, read `references/odoo-qweb-positioning.md` before writing report code.

Use this priority order:

1. Implement absolute positions from `manifest.json.qweb_layout_spec`.
2. Use `manifest.json.report_blueprint` to classify static labels, dynamic values, line items, and signature/footer regions.
3. Use `annotated/page-*-grid.png` only to audit or resolve ambiguous regions.

Minimum placement pattern:

```html
<div class="page" style="position: relative; width: 210mm; height: 297mm;">
  <div style="position: absolute; left: 21mm; top: 29.7mm; width: 84mm; height: 29.7mm;">
    ...
  </div>
</div>
```

Compute from manifest:

- `left = bbox_mm[0]`
- `top = bbox_mm[1]`
- `width = bbox_mm[2] - bbox_mm[0]`
- `height = bbox_mm[3] - bbox_mm[1]`

For fixed labels and titles, apply the generated text-fit rules. At minimum use:

```css
white-space: nowrap;
overflow: hidden;
box-sizing: border-box;
```

If a title such as `SURAT JALAN` wraps, the layout is not acceptable. Increase its measured width or reduce font size within the same bbox instead of allowing wrapping.

## Dynamic Header Overflow Guard

Header values such as customer name, delivery address, warehouse address, PIC, phone, document numbers, and contract numbers must never push boxes, tables, or signature areas downward.

Treat every header value as a fixed region, not normal HTML flow:

- Put each header value group in an absolute-positioned container with explicit `left`, `top`, `width`, and `height` in `mm`.
- Set `overflow: hidden`, `box-sizing: border-box`, and a fixed `line-height`.
- If multiple lines are allowed, make the line count explicit from the template height, for example `height: 18mm; line-height: 3mm;` means at most 6 lines.
- If only one line is allowed, use `white-space: nowrap; text-overflow: ellipsis; overflow: hidden;`.
- Do not let dynamic header values use unrestricted `<div>` flow, auto height, natural paragraphs, or content-driven margins.
- Do not place the item table after a dynamic header block in normal document flow. The table/header grid must be absolutely positioned from measured `bbox_mm` or `structural_grid` coordinates.
- For very long values, prefer a model/helper method that formats and truncates to the template's allowed line count. Do not rely on wkhtmltopdf to line-clamp consistently.

Before accepting a report, run a long-value stress render: use or create a record with long partner name, long address, long document number, and long PIC. The rendered box/table/signature coordinates must stay unchanged; only the text inside its fixed bbox may be clipped, wrapped within the allowed line count, or reduced.

## Static vs Dynamic Interpretation

After coordinates are generated, read `manifest.json.report_blueprint`.

Use these rules:

- `static_text`: labels/headings printed exactly in the template, for example `DELIVERY TO`, `WAREHOUSE`, `NO MOBIL`, `QTY`.
- `dynamic_value`: values likely loaded from an Odoo record, for example dates, document numbers, partner names, addresses, quantities, serial/drum numbers.
- `candidate_value`: unclear text; review manually against the Odoo model.
- `static_assets`: boxes, borders, logos, watermarks, or decorative images. Implement as CSS/static image unless the source must come from Odoo binary fields.

Field suggestions are heuristic. Before writing QWeb, verify each suggested field against the target Odoo model and module code.

## Line Pagination

For reports with item tables, use `--max-lines-per-page` when the template has a known row limit.

```bash
--max-lines-per-page 10
```

QWeb implementation should:

- split line records into page chunks using the max line count;
- repeat the static header/table header on each continuation page;
- keep footer/signature blocks only where the business format expects them;
- preserve the same `bbox_mm` table region for each page.

## Workflow

1. Confirm paper size: A4, A5, Legal, Letter, or exact custom dimensions.
2. For camera photos, run with `--preprocess document`; for clean PDF/scans, use `--preprocess none`.
3. Inspect `clean/page-*.png` first. It must look like a scanner output: only the document, no table/background, no perspective skew.
4. Inspect `annotated/page-*-grid.png`; grid must cover only the rectified paper.
5. Read `manifest.json.report_blueprint` to separate static labels/assets from dynamic Odoo values.
6. Read `manifest.json.qweb_layout_spec` and use its CSS-ready `bbox_mm` placements for QWeb.
7. Use `qweb_layout_spec.pages[].structural_grid` for measured box/table/signature widths before writing CSS.
8. Verify every `odoo_field_suggestion` against actual Odoo model fields before coding.
9. Set or review `--max-lines-per-page` for item tables and continuation pages.
10. Generate the report.
11. Render the generated PDF back to PNG.
12. Run `scripts/compare_report_render.py` against `clean/page-*.png`.
13. Inspect `compare_metrics.json` and `diff-overlay.png`.
14. In structure mode, inspect `structure_line_deltas_mm` and fix measured millimeter coordinates or paperformat scaling before visual tweaks.
15. Render again until title/header boxes/table/signatures align.

## Common Mistakes

- Using image pixels directly in QWeb. Convert to `mm` using the manifest.
- Treating OCR output as authoritative. Use it as a draft and verify visually.
- Treating `odoo_field_suggestion` as final. It is only a draft mapping.
- Ignoring page size. A4 and Letter coordinates produce different `bbox_mm`.
- Ignoring line overflow. Fixed report templates need explicit item chunking across pages.
- Putting grid over the camera photo instead of over the rectified document.
- Rebuilding from a screenshot crop without matching the intended paper size.
- Moving elements by eye after a manifest exists. Update coordinates deliberately.
- Treating `qweb_layout_spec` as optional. Without it, agents tend to recreate the old visual-grid-only failure mode.
- Accepting wrapped fixed headings or labels in QWeb. Fixed report headings must stay inside one bbox.
- Allowing long dynamic header values to create normal-flow height. Header values must be clipped, truncated, or fixed-line wrapped inside their measured bbox; they must not push the table or footer down.
- Skipping the comparator. A grid helps placement, but only render-vs-reference metrics expose wkhtmltopdf scaling, font, border, and page-margin drift.
- Ignoring `structural_grid` for tables. Raw contour boxes can be fragmented by OCR, handwriting, or scan noise; structural grid is the better source for repeated box/table columns.
- Ignoring paperformat calibration. For Odoo wkhtmltopdf reports, check `disable_shrinking`, `dpi`, margins, and blank overflow pages before compensating every element manually.
