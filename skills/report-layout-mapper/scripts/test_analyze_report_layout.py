import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).with_name("analyze_report_layout.py")


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_report_layout", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalyzeReportLayoutTests(unittest.TestCase):
    def test_pixel_bbox_converts_to_normalized_coordinates(self):
        mod = load_module()

        bbox = mod.pixel_bbox_to_norm((170, 240, 850, 480), width=1700, height=2400)

        self.assertEqual(bbox, [100, 100, 500, 200])

    def test_normalized_bbox_converts_to_a4_millimeters(self):
        mod = load_module()

        bbox = mod.norm_bbox_to_mm([100, 100, 500, 200], paper_mm=(210, 297))

        self.assertEqual(bbox, [21.0, 29.7, 105.0, 59.4])

    def test_missing_full_detection_dependencies_report_opencv_install_command(self):
        mod = load_module()

        messages = mod.missing_dependency_messages(
            detect="full",
            preprocess="none",
            has_module=lambda name: name in {"fitz", "PIL"},
            has_executable=lambda name: False,
        )

        self.assertEqual(messages, ["python3 -m pip install numpy opencv-python"])

    def test_full_detection_does_not_require_ocr_dependencies(self):
        mod = load_module()

        messages = mod.missing_dependency_messages(
            detect="full",
            preprocess="none",
            has_module=lambda name: name in {"fitz", "PIL", "numpy", "cv2"},
            has_executable=lambda name: False,
        )

        self.assertEqual(messages, [])

    def test_document_preprocess_dependencies_report_opencv_without_ocr(self):
        mod = load_module()

        messages = mod.missing_dependency_messages(
            detect="grid",
            preprocess="document",
            has_module=lambda name: name in {"fitz", "PIL"},
            has_executable=lambda name: True,
        )

        self.assertEqual(messages, ["python3 -m pip install numpy opencv-python"])

    def test_resolve_standard_and_custom_paper_sizes(self):
        mod = load_module()

        self.assertEqual(mod.resolve_paper_mm("a5", None, None), (148.0, 210.0))
        self.assertEqual(mod.resolve_paper_mm("legal", None, None), (215.9, 355.6))
        self.assertEqual(mod.resolve_paper_mm("custom", 200, 300), (200.0, 300.0))

    def test_paper_pixel_size_uses_physical_dimensions_and_dpi(self):
        mod = load_module()

        self.assertEqual(mod.paper_pixel_size((210.0, 297.0), dpi=220), (1819, 2572))

    def test_manifest_writer_uses_normalized_coordinate_system(self):
        mod = load_module()
        page = mod.PageAnalysis(
            page=1,
            pixel_size=(1700, 2400),
            paper_mm=(210, 297),
            elements=[
                mod.Element(
                    id="p1_box_001",
                    type="box",
                    bbox_px=[170, 240, 850, 480],
                    bbox_norm=[100, 100, 500, 200],
                    bbox_mm=[21.0, 29.7, 105.0, 59.4],
                    confidence=0.8,
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = mod.write_manifest(
                out_dir=Path(tmp),
                source=Path("sample.pdf"),
                target="odoo-qweb",
                pages=[page],
            )

            data = json.loads(manifest_path.read_text())

        self.assertEqual(data["coordinate_system"], "normalized_0_1000_top_left")
        self.assertEqual(data["pages"][0]["elements"][0]["bbox_mm"], [21.0, 29.7, 105.0, 59.4])

    def test_classifies_static_labels_and_dynamic_values(self):
        mod = load_module()

        self.assertEqual(mod.classify_text_role("DELIVERY TO"), "static_text")
        self.assertEqual(mod.classify_text_role("03-Jun-26"), "dynamic_value")
        self.assertEqual(mod.suggest_odoo_field("SJ NO.")["field"], "name")
        self.assertEqual(mod.suggest_odoo_field("NO MOBIL")["field"], "vehicle_no")

    def test_builds_report_blueprint_with_line_pagination(self):
        mod = load_module()
        page = mod.PageAnalysis(
            page=1,
            pixel_size=(1000, 1000),
            paper_mm=(210, 297),
            elements=[
                mod.Element("title", "text", [450, 160, 610, 185], [450, 160, 610, 185], [94.5, 47.52, 128.1, 54.95], 1.0, "SURAT JALAN"),
                mod.Element("label", "text", [100, 200, 280, 220], [100, 200, 280, 220], [21, 59.4, 58.8, 65.34], 1.0, "DELIVERY TO"),
                mod.Element("value", "text", [100, 230, 280, 250], [100, 230, 280, 250], [21, 68.31, 58.8, 74.25], 1.0, "PT. PLN (Persero)"),
                mod.Element("header", "text", [130, 400, 800, 420], [130, 400, 800, 420], [27.3, 118.8, 168, 124.74], 1.0, "NO CODE DESCRIPTION UNIT QTY"),
                mod.Element("row1", "text", [130, 430, 800, 445], [130, 430, 800, 445], [27.3, 127.71, 168, 132.17], 1.0, "1 F00050 Cable Drum 1"),
                mod.Element("footer", "text", [130, 780, 800, 805], [130, 780, 800, 805], [27.3, 231.66, 168, 239.09], 1.0, "Receiver Driver Warehouse Approval"),
            ],
        )

        blueprint = mod.build_report_blueprint([page], max_lines_per_page=10)

        self.assertEqual(blueprint["report_kind"], "delivery_order")
        self.assertEqual(blueprint["pagination"]["line_items"]["max_lines_first_page"], 10)
        self.assertEqual(blueprint["regions"]["line_items"]["header_element_id"], "header")
        self.assertEqual(blueprint["text_roles"]["title"]["role"], "static_text")
        self.assertEqual(blueprint["text_roles"]["value"]["role"], "dynamic_value")

    def test_builds_qweb_layout_spec_with_css_and_text_fit_constraints(self):
        mod = load_module()
        page = mod.PageAnalysis(
            page=1,
            pixel_size=(1000, 1000),
            paper_mm=(210, 297),
            elements=[
                mod.Element("title", "text", [650, 180, 820, 205], [650, 180, 820, 205], [136.5, 53.46, 172.2, 60.88], 1.0, "SURAT JALAN"),
                mod.Element("header", "text", [130, 400, 800, 420], [130, 400, 800, 420], [27.3, 118.8, 168, 124.74], 1.0, "NO CODE DESCRIPTION UNIT QTY"),
                mod.Element("footer", "text", [130, 780, 800, 805], [130, 780, 800, 805], [27.3, 231.66, 168, 239.09], 1.0, "Receiver Driver Warehouse Approval"),
                mod.Element("sign_box", "box", [100, 820, 900, 950], [100, 820, 900, 950], [21.0, 243.54, 189.0, 282.15], 0.9),
            ],
            structural_grid={
                "vertical_lines_mm": [21.0, 105.0, 189.0],
                "horizontal_lines_mm": [243.54, 282.15],
                "cells_mm": [[21.0, 243.54, 105.0, 282.15]],
                "cells_css": [{"bbox_mm": [21.0, 243.54, 105.0, 282.15], "css": "position: absolute;"}],
            },
        )
        blueprint = mod.build_report_blueprint([page], max_lines_per_page=10)

        spec = mod.build_qweb_layout_spec([page], blueprint)
        page_spec = spec["pages"][0]
        title_spec = page_spec["static_text"][0]

        self.assertEqual(spec["coordinate_source"], "bbox_mm")
        self.assertIn("left: 136.5mm", title_spec["css"])
        self.assertTrue(title_spec["fit"]["nowrap"])
        self.assertIn("white-space: nowrap", title_spec["css"])
        self.assertEqual(page_spec["line_table"]["bbox_mm"], [27.3, 124.74, 168.0, 231.66])
        self.assertEqual(page_spec["signature_boxes"][0]["source"], "text")
        self.assertEqual(page_spec["structural_grid"]["cells_mm"][0], [21.0, 243.54, 105.0, 282.15])

    def test_manifest_writer_includes_qweb_layout_spec(self):
        mod = load_module()
        page = mod.PageAnalysis(
            page=1,
            pixel_size=(1000, 1000),
            paper_mm=(210, 297),
            elements=[
                mod.Element("title", "text", [650, 180, 820, 205], [650, 180, 820, 205], [136.5, 53.46, 172.2, 60.88], 1.0, "SURAT JALAN"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = mod.write_manifest(
                out_dir=Path(tmp),
                source=Path("sample.png"),
                target="odoo-qweb",
                pages=[page],
            )

            data = json.loads(manifest_path.read_text())

        self.assertIn("qweb_layout_spec", data)
        self.assertEqual(data["qweb_layout_spec"]["pages"][0]["paper_mm"], [210, 297])

    def test_detects_structural_grid_lines_and_cells(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "grid.png"
            image = Image.new("RGB", (1000, 1000), "white")
            draw = ImageDraw.Draw(image)
            for x in (100, 500, 900):
                draw.line((x, 100, x, 500), fill="black", width=4)
            for y in (100, 300, 500):
                draw.line((100, y, 900, y), fill="black", width=4)
            image.save(image_path)

            grid = mod.detect_structural_grid(image_path, paper_mm=(200, 200))

        self.assertGreaterEqual(len(grid["vertical_lines_mm"]), 3)
        self.assertGreaterEqual(len(grid["horizontal_lines_mm"]), 3)
        self.assertIn(80.0, grid["column_widths_mm"])
        self.assertIn(40.0, grid["row_heights_mm"])
        self.assertTrue(any(
            all(abs(actual - expected) < 0.5 for actual, expected in zip(cell, [20.0, 20.0, 100.0, 60.0]))
            for cell in grid["cells_mm"]
        ))
        self.assertIn("border", grid["cells_css"][0]["css"])

    def test_prompt_includes_render_comparator_command(self):
        mod = load_module()
        page = mod.PageAnalysis(
            page=1,
            pixel_size=(1000, 1000),
            paper_mm=(210, 297),
            elements=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            prompt_path = mod.write_prompt(
                out_dir=Path(tmp),
                source=Path("sample.png"),
                target="odoo-qweb",
                pages=[page],
            )

            prompt = prompt_path.read_text()

        self.assertIn("compare_report_render.py", prompt)
        self.assertIn("--reference <analysis-out>/clean/page-001.png", prompt)
        self.assertIn("structural_grid", prompt)
        self.assertIn("--mode edge", prompt)
        self.assertIn("--mode structure --crop-mm", prompt)
        self.assertIn("diff-overlay.png", prompt)


if __name__ == "__main__":
    unittest.main()
