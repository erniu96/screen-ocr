"""Geometry reconstruction regressions using synthetic public examples."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from wxocr_native import cli
from wxocr_native.layout import LayoutError, reconstruct_text


def box(text, left, top, right, bottom):
    return {"text": text, "left": left, "top": top, "right": right,
            "bottom": bottom, "rate": 0.9}


def result(*boxes):
    return {"errcode": 0, "width": 600, "height": 400, "ocr_response": list(boxes)}


class LayoutTests(unittest.TestCase):
    def test_fragments_restore_visual_lines_without_correcting_missed_letters(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/synthetic-layout-fragments.json").read_text())
        original = copy.deepcopy(fixture["result"])
        layout = reconstruct_text(original)
        self.assertEqual([line["text"] for line in layout["lines"]], fixture["expected_lines"])
        self.assertEqual([line["source_indices"] for line in layout["lines"]],
                         fixture["expected_source_indices"])
        self.assertEqual(layout["source_fragment_count"], 12)
        self.assertEqual(layout["output_line_count"], 7)
        self.assertEqual(sorted(index for line in layout["lines"] for index in line["source_indices"]),
                         list(range(12)))
        self.assertEqual(original, fixture["result"])
        self.assertIn("impove", layout["text"])
        self.assertNotIn("improve", layout["text"])

    def test_shuffled_word_fragments_and_lines_use_geometry(self):
        layout = reconstruct_text(result(box("ions", 50, 10, 90, 30),
                                         box("next row", 10, 50, 90, 70),
                                         box("quest", 0, 10, 49, 30)))
        self.assertEqual(layout["text"], "questions\nnext row")
        self.assertEqual(layout["lines"][0]["source_indices"], [2, 0])
        self.assertEqual(layout["lines"][0]["separators"], [""])

    def test_visible_word_gap_inserts_space_but_existing_space_is_not_duplicated(self):
        layout = reconstruct_text(result(box("world", 60, 10, 110, 30),
                                         box("hello", 0, 10, 50, 30)))
        self.assertEqual(layout["text"], "hello world")
        layout = reconstruct_text(result(box("hello ", 0, 10, 50, 30),
                                         box("world", 60, 10, 110, 30)))
        self.assertEqual(layout["text"], "hello world")
        self.assertEqual(layout["lines"][0]["separators"], [""])

    def test_adjacent_cjk_and_punctuation_preserve_characters_and_case(self):
        layout = reconstruct_text(result(box("。", 42.5, 10, 60, 30),
                                         box("中文", 0, 10, 40, 30),
                                         box("Raw_Case()", 0, 50, 100, 70)))
        self.assertEqual(layout["text"], "中文。\nRaw_Case()")

    def test_wide_column_gutter_stays_separate_without_inventing_column_order(self):
        layout = reconstruct_text(result(box("left 2", 0, 50, 50, 70),
                                         box("right 1", 250, 10, 310, 30),
                                         box("left 1", 0, 10, 50, 30),
                                         box("right 2", 250, 50, 310, 70)))
        self.assertEqual(layout["text"], "left 1\nright 1\nleft 2\nright 2")
        self.assertEqual(layout["warnings"], ["wide_gaps_kept_separate"])
        self.assertTrue(all(len(line["source_indices"]) == 1 for line in layout["lines"]))

    def test_overlapping_alternatives_are_not_deduplicated_or_spliced(self):
        layout = reconstruct_text(result(box("abc", 0, 10, 30, 30),
                                         box("abd", 1, 10, 31, 30)))
        self.assertEqual(layout["text"], "abc\nabd")
        self.assertEqual(layout["warnings"], ["overlapping_boxes_kept_separate"])

    def test_tall_box_does_not_chain_distinct_rows(self):
        layout = reconstruct_text(result(box("top", 0, 0, 30, 20),
                                         box("tall", 40, 0, 80, 60),
                                         box("bottom", 0, 40, 60, 60)))
        self.assertEqual(layout["output_line_count"], 3)
        self.assertEqual(layout["text"], "top\ntall\nbottom")

    def test_blank_result_is_empty(self):
        layout = reconstruct_text(result())
        self.assertEqual(layout["text"], "")
        self.assertEqual(layout["lines"], [])
        self.assertEqual(layout["source_fragment_count"], 0)

    def test_invalid_geometry_is_rejected_instead_of_guessed(self):
        for fragment in (box("x", 20, 10, 10, 30), box("x", 0, 10, 10, 10),
                         box("x", float("nan"), 10, 10, 30),
                         box("x", 0, 10, float("inf"), 30), box("x", False, 10, 10, 30)):
            with self.subTest(fragment=fragment), self.assertRaises(LayoutError):
                reconstruct_text(result(fragment))

    def test_exact_field_contract_does_not_accept_coordinate_aliases(self):
        invalid = {"text": "x", "x": 0, "y": 10, "width": 10, "height": 20}
        with self.assertRaises(KeyError):
            reconstruct_text(result(invalid))


class LayoutIntegrationTests(unittest.TestCase):
    def test_cli_text_is_opt_in_and_default_json_keeps_original_order(self):
        raw = result(box("ions", 50, 10, 90, 30), box("quest", 0, 10, 49, 30))
        engine = Mock()
        engine.recognize.return_value = raw
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            for extra in ([], ["--text"]):
                with self.subTest(extra=extra), patch.object(cli, "NativeEngine", return_value=engine):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(cli.main(["recognize", image.name, *extra]), 0)
                    if extra:
                        self.assertEqual(output.getvalue(), "questions\n")
                    else:
                        self.assertEqual(json.loads(output.getvalue()), {"result": raw})


if __name__ == "__main__":
    unittest.main()
