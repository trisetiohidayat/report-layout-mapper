import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

MODULE_PATH = Path(__file__).with_name("compare_report_render.py")


def load_module():
    spec = importlib.util.spec_from_file_location("compare_report_render", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_box_image(path: Path, offset: int = 0, text: str = "SURAT JALAN"):
    image = Image.new("RGB", (160, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30 + offset, 40, 120 + offset, 90), outline="black", width=2)
    draw.text((38 + offset, 58), text, fill="black")
    image.save(path)


def make_line_image(path: Path, extra_vertical: bool = False, omit_box: bool = False):
    image = Image.new("RGB", (160, 220), "white")
    draw = ImageDraw.Draw(image)
    if not omit_box:
        draw.rectangle((30, 40, 120, 90), outline="black", width=2)
    if extra_vertical:
        draw.line((140, 35, 140, 100), fill="black", width=2)
    image.save(path)


def make_double_edge_image(path: Path):
    image = Image.new("RGB", (160, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.line((40, 30, 40, 120), fill="black", width=1)
    draw.line((41, 30, 41, 120), fill="black", width=1)
    image.save(path)


class CompareReportRenderTests(unittest.TestCase):
    def test_identical_images_have_full_similarity(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            make_box_image(reference)
            make_box_image(rendered)

            metrics = mod.compare_images(reference, rendered, tmp_path / "out")

        self.assertEqual(metrics["mismatch_pixels"], 0)
        self.assertEqual(metrics["similarity"], 1.0)
        self.assertIsNone(metrics["diff_bbox_px"])

    def test_shifted_render_reports_mismatch_outputs(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            out = tmp_path / "out"
            make_box_image(reference)
            make_box_image(rendered, offset=12)

            metrics = mod.compare_images(reference, rendered, out)
            stored = json.loads((out / "compare_metrics.json").read_text())
            diff_mask_exists = Path(metrics["diff_mask"]).exists()
            diff_overlay_exists = Path(metrics["diff_overlay"]).exists()

        self.assertGreater(metrics["mismatch_pixels"], 0)
        self.assertLess(metrics["similarity"], 1.0)
        self.assertIsNotNone(metrics["diff_bbox_px"])
        self.assertEqual(stored["diff_overlay"], metrics["diff_overlay"])
        self.assertTrue(diff_mask_exists)
        self.assertTrue(diff_overlay_exists)

    def test_crop_mm_limits_comparison_region(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            out = tmp_path / "out"
            make_box_image(reference)
            make_box_image(rendered, offset=12)

            metrics = mod.compare_images(
                reference,
                rendered,
                out,
                crop_mm=(0, 0, 80, 110),
                paper_mm=(160, 220),
            )

        self.assertEqual(metrics["pixel_size"], [80, 110])
        self.assertEqual(metrics["crop_px"], [0, 0, 80, 110])
        self.assertEqual(metrics["crop_mm"], [0, 0, 80, 110])

    def test_structure_mode_reduces_text_value_noise(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            make_box_image(reference, text="SJ-001")
            make_box_image(rendered, text="DO-999")

            edge = mod.compare_images(reference, rendered, tmp_path / "edge")
            structure = mod.compare_images(reference, rendered, tmp_path / "structure", mode="structure")

        self.assertEqual(structure["mode"], "structure")
        self.assertGreater(edge["mismatch_pixels"], structure["mismatch_pixels"])
        self.assertGreater(structure["similarity"], edge["similarity"])

    def test_structure_mode_reports_line_deltas_in_mm(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            out = tmp_path / "out"
            make_box_image(reference)
            make_box_image(rendered, offset=3)

            metrics = mod.compare_images(
                reference,
                rendered,
                out,
                mode="structure",
                paper_mm=(160, 220),
                line_match_tolerance_mm=5,
            )
            stored = json.loads((out / "compare_metrics.json").read_text())

        diagnostics = metrics["structure_line_deltas_mm"]
        self.assertEqual(stored["structure_line_deltas_mm"], diagnostics)
        self.assertGreater(len(diagnostics["vertical"]["matched"]), 0)
        self.assertTrue(any(line["abs_delta_mm"] > 0 for line in diagnostics["vertical"]["matched"]))
        self.assertIn("horizontal", diagnostics)

    def test_structure_mode_reports_missing_and_extra_lines(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference = tmp_path / "reference.png"
            rendered = tmp_path / "rendered.png"
            make_line_image(reference)
            make_line_image(rendered, extra_vertical=True, omit_box=True)

            metrics = mod.compare_images(
                reference,
                rendered,
                tmp_path / "out",
                mode="structure",
                paper_mm=(160, 220),
            )

        diagnostics = metrics["structure_line_deltas_mm"]
        self.assertGreater(len(diagnostics["horizontal"]["missing_reference_lines_mm"]), 0)
        self.assertGreater(len(diagnostics["vertical"]["extra_rendered_lines_mm"]), 0)

    def test_structure_line_extraction_merges_double_edges(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            image_path = tmp_path / "double-edge.png"
            make_double_edge_image(image_path)
            image = mod.prepare_structure_image(image_path)

            loose = mod.extract_structure_line_positions(
                image,
                origin_mm=(0, 0),
                size_mm=(160, 220),
                min_line_length_mm=6,
                line_merge_tolerance_mm=2,
            )

        self.assertEqual(len(loose["vertical_lines_mm"]), 1)


if __name__ == "__main__":
    unittest.main()
