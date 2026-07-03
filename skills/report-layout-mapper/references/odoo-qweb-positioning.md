# Odoo QWeb Positioning

Use this reference after running `scripts/analyze_report_layout.py` with `--target odoo-qweb`.

For camera photos, use `--preprocess document` first. QWeb coordinates must come from the scanner-like `clean/page-*.png`, not the raw photo.

## Page Container

Use the same physical page size as `manifest.json`. For A4:

```html
<div class="page" style="position: relative; width: 210mm; height: 297mm;">
  <!-- absolute-positioned elements -->
</div>
```

Avoid pixel units in QWeb PDF layout. wkhtmltopdf/browser rendering can scale pixels differently across environments; millimeters track paper size directly.

If `manifest.json` says:

```json
"paper_mm": [215.9, 355.6]
```

then the QWeb page container must use `width: 215.9mm; height: 355.6mm;`.

## Convert Manifest Boxes

Each element has:

```json
"bbox_mm": [21.0, 29.7, 105.0, 59.4]
```

Map it to CSS:

```text
left:   21.0mm
top:    29.7mm
width:  84.0mm
height: 29.7mm
```

Formula:

```text
left = x1
top = y1
width = x2 - x1
height = y2 - y1
```

## Static Text, Values, and Assets

Use `manifest.json.report_blueprint` before coding:

- `static_text`: implement as literal QWeb text or CSS-styled headings.
- `dynamic_value`: implement with `t-esc`, `t-field`, or computed helper values.
- `candidate_value`: inspect manually before mapping.
- `static_assets`: implement with CSS borders/backgrounds or an image asset when exact visual fidelity matters.

Example:

```html
<span>DELIVERY TO</span>
<span t-esc="doc.partner_id.name"/>
```

Do not blindly trust field suggestions. Verify the real field names on the target model.

## Header Dynamic Values

Header values must be layout-contained. A long customer name, address, PIC, or document number must not change the `top` position of the table or footer.

Use fixed boxes:

```html
<div style="
  position: absolute;
  left: 22.5mm;
  top: 65mm;
  width: 82mm;
  height: 18mm;
  line-height: 3mm;
  overflow: hidden;
  box-sizing: border-box;
">
  <span t-esc="doc.tri_report_header_text('delivery_address')"/>
</div>
```

For single-line values:

```html
<div style="
  position: absolute;
  left: 153mm;
  top: 93.8mm;
  width: 47mm;
  height: 4.5mm;
  line-height: 4.1mm;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
">
  <span t-esc="doc.name"/>
</div>
```

For multi-line values, prefer preparing text in Python to a fixed number of lines. Do not rely on CSS `line-clamp` for wkhtmltopdf-critical reports.

The item table must be absolutely positioned independently of header content:

```html
<div class="line-table" style="position:absolute; left:22.35mm; top:121.77mm; width:177.66mm;">
  ...
</div>
```

Stress-test the report with maximum-length header values before accepting it. The structure comparator should show the same table/footer line positions before and after the long-value render.

## Line Items and Continuation Pages

Use `report_blueprint.pagination.line_items.max_lines_first_page` as a drafting hint. If the business template has a known limit, prefer the explicit `--max-lines-per-page` value used during analysis.

Typical QWeb pattern:

```xml
<t t-set="chunks" t-value="[doc.move_line_ids[i:i + 10] for i in range(0, len(doc.move_line_ids), 10)]"/>
<t t-foreach="chunks" t-as="chunk">
  <div class="page" style="position: relative; width: 210mm; height: 297mm;">
    <!-- repeat title/header/table header -->
    <t t-foreach="chunk" t-as="line">
      <!-- absolutely place rows using row index and row height -->
    </t>
    <!-- footer/signatures only on final page if required -->
  </div>
</t>
```

For production Odoo code, prefer computing chunks in Python when the expression gets complex.

## Example Element

```html
<div style="
  position: absolute;
  left: 21mm;
  top: 29.7mm;
  width: 84mm;
  height: 29.7mm;
">
  <span t-esc="doc.name"/>
</div>
```

## Validation Loop

1. Build the QWeb report using `bbox_mm`.
2. Render the generated PDF to PNG.
3. Compare generated PNG against `clean/page-*.png` with:

```bash
python3 scripts/compare_report_render.py \
  --reference <analysis-out>/clean/page-001.png \
  --rendered <generated-report-page-001.png> \
  --out <analysis-out>/compare \
  --mode edge
```

For focused structure checks that ignore dynamic text/value differences:

```bash
python3 scripts/compare_report_render.py \
  --reference <analysis-out>/clean/page-001.png \
  --rendered <generated-report-page-001.png> \
  --out <analysis-out>/compare-header-structure \
  --mode structure \
  --crop-mm "15,40,205,135" \
  --min-line-length-mm 6 \
  --line-merge-tolerance-mm 0.7
```

4. Inspect `compare_metrics.json` and `diff-overlay.png`.
5. Use `annotated/page-*-grid.png` to interpret mismatch locations.
6. In `--mode structure`, use `compare_metrics.json.structure_line_deltas_mm` for actionable corrections:
   - positive `delta_mm`: rendered line is right/down from the reference; reduce CSS `left`/`top`;
   - negative `delta_mm`: rendered line is left/up from the reference; increase CSS `left`/`top`;
   - repeated deltas across many lines: check report paperformat scaling before moving individual boxes.
   - nearby missing/extra pairs within roughly one border thickness: increase `--line-merge-tolerance-mm` or inspect the overlay before editing QWeb.
7. Adjust coordinates in `mm`, not pixels.

Treat the comparator as a drift detector, not a perfect pass/fail oracle. Scans contain noise and business data can differ, so prioritize region crops. Use `edge` for title/static text/name drift and `structure` for boxes, table columns, totals, and signature-box positions.

For Odoo PDF reports, verify the paperformat before fine tuning:

- use the intended paper size and zero or known margins;
- set `disable_shrinking` when exact physical coordinates matter;
- prefer `dpi=96` with wkhtmltopdf 0.12.2+ so Odoo's `--zoom 96/dpi` does not introduce unintended scaling;
- if a blank trailing page appears, check whether the report container plus wkhtml/body margins exceeds the paper height.

## Pitfalls

- Do not use HTML flow layout for fixed templates; use absolute placement for template-matched fields.
- Do not assume browser preview and PDF output have identical scaling.
- Do not rely on OCR text order for business logic.
- Do not let long header values use auto height or normal document flow; clip, truncate, or fixed-line-wrap them inside their bbox.
- Do not let table rows flow naturally when the source template has a fixed row box; chunk and position rows deliberately.
- Keep boxes/tables as CSS borders only when they match the template; otherwise use static background/reference assets deliberately.
- Do not accept a layout before rendering PDF to PNG. Browser HTML and wkhtmltopdf can differ in margins, font metrics, and border widths.
