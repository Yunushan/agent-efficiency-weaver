"""User-supplied benchmark labels remain data in Markdown table cells."""

import json
import tempfile
import unittest
from pathlib import Path

from test_cli_exports import invoke

from agentweaver.report import as_markdown, summarize


def benchmark_row():
    return {
        "task_id": "example", "run_id": "1", "source": "synthetic", "status": "passed",
        "agent": "agent|extra", "variant": "base\r\nsecond\nthird\rfourth <img src='example'>",
        "input_tokens": 100, "output_tokens": 50, "billed_cost_usd": 0.01,
    }


class MarkdownReportTests(unittest.TestCase):
    def assert_single_six_column_row(self, output):
        rows = [line for line in output.splitlines() if line.startswith("|")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(line.count("|") == 7 for line in rows))
        self.assertEqual(output.splitlines()[-1], rows[-1])
        return rows[-1]

    def test_pipes_line_breaks_and_html_keep_table_structure(self):
        source = benchmark_row()
        result = summarize([source])
        original = json.dumps(result, sort_keys=True)
        row = self.assert_single_six_column_row(as_markdown(result))
        self.assertIn("agent&#124;extra", row)
        self.assertIn("base<br>second<br>third<br>fourth", row)
        self.assertIn("&lt;img src=&#x27;example&#x27;&gt;", row)
        self.assertNotIn("<img", row)
        self.assertEqual(json.dumps(result, sort_keys=True), original)

    def test_markdown_links_images_and_emphasis_are_literal_labels(self):
        source = benchmark_row()
        source["variant"] = "![image](https://example.invalid) **bold** _emphasis_ `code` ~~strike~~ \\"
        row = self.assert_single_six_column_row(as_markdown(summarize([source])))
        self.assertIn(r"\!\[image\]\(https://example.invalid\)", row)
        self.assertIn(r"\*\*bold\*\*", row)
        self.assertIn(r"\_emphasis\_", row)
        self.assertIn(r"\`code\`", row)
        self.assertIn(r"\~\~strike\~\~", row)
        self.assertIn(r"\\", row)

    def test_cli_markdown_escapes_valid_jsonl_labels_without_changing_json(self):
        source = benchmark_row()
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "synthetic.jsonl"
            fixture.write_text(json.dumps(source) + "\n", encoding="utf-8")
            arguments = ("report", "--input", str(fixture))
            code, output, error = invoke(*arguments)
            self.assertEqual(code, 0, error)
            self.assert_single_six_column_row(output)
            self.assertNotIn("<img", output)
            code, output, error = invoke(*arguments, "--format", "json")
            self.assertEqual(code, 0, error)
            group = json.loads(output)["groups"][0]
            self.assertEqual(group["agent"], source["agent"])
            self.assertEqual(group["variant"], source["variant"])
            self.assertEqual(group["tokens_per_successful_task"], 150)
            self.assertEqual(group["cost_per_successful_task_usd"], 0.01)


if __name__ == "__main__":
    unittest.main()
