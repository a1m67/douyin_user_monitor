from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from douyin_user_monitor.__main__ import main
from douyin_user_monitor.parser_eval import DEFAULT_FIXTURE_PATH, evaluate_parser_golden


class ParserGoldenEvaluationTests(unittest.TestCase):
    def test_committed_golden_corpus_matches_exactly_offline(self):
        report = evaluate_parser_golden()

        self.assertEqual(report["cases"], 24)
        self.assertEqual(report["passed"], 24)
        self.assertEqual(report["failed"], 0)
        self.assertTrue(report["ok"])
        self.assertEqual(set(report["field_accuracy"].values()), {1.0})

    def test_cli_json_reports_failures_and_nonzero_exit(self):
        cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
        cases[0]["expected"]["episode_number"] = 999
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with patch("sys.argv", ["douyin_user_monitor", "parser-eval", "--json", "--file", str(path)]):
                with redirect_stdout(output):
                    exit_code = main()

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["failures"][0]["id"], "chapter-1-no-title")


if __name__ == "__main__":
    unittest.main()
