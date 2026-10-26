import json
import tempfile
import unittest
from pathlib import Path

from test_cli_exports import invoke

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'benchmarks/fixtures/synthetic_runs.jsonl'


class BenchmarkCliTests(unittest.TestCase):
    def test_report_json_is_parseable_on_stdout_and_in_file(self):
        arguments = ('report', '--input', str(FIXTURE), '--format', 'json')
        code, output, error = invoke(*arguments)
        self.assertEqual(code, 0, error)
        self.assertTrue(output.endswith('\n'))
        result = json.loads(output)
        self.assertEqual(len(result['groups']), 2)
        self.assertTrue(all(group['source'] == 'synthetic' for group in result['groups']))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'nested/report.json'
            code, output, error = invoke(*arguments, '--output', str(destination))
            self.assertEqual(code, 0, error)
            content = destination.read_text(encoding='utf-8')
            self.assertTrue(content.endswith('\n'))
            self.assertEqual(json.loads(content), result)

    def test_compare_json_is_parseable_on_stdout_and_in_file(self):
        arguments = ('compare', '--input', str(FIXTURE), '--agent', 'kilo', '--source', 'synthetic')
        code, output, error = invoke(*arguments)
        self.assertEqual(code, 0, error)
        self.assertTrue(output.endswith('\n'))
        result = json.loads(output)
        self.assertEqual(result['matched_attempts'], 2)
        self.assertEqual(result['source'], 'synthetic')
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'nested/compare.json'
            code, output, error = invoke(*arguments, '--output', str(destination))
            self.assertEqual(code, 0, error)
            content = destination.read_text(encoding='utf-8')
            self.assertTrue(content.endswith('\n'))
            self.assertEqual(json.loads(content), result)


if __name__ == '__main__':
    unittest.main()
