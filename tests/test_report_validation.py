import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_cli_exports import invoke

from agentweaver.compare import compare_runs
from agentweaver.config import WeaverError
from agentweaver.report import read_runs, summarize


def record(**changes):
    return dict(task_id='example', run_id='1', agent='kilo', variant='baseline',
                source='synthetic', status='passed', **changes)


class BenchmarkValidationTests(unittest.TestCase):
    def read_text(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.jsonl'
            path.write_text(content, encoding='utf-8')
            return read_runs(path)

    def test_duplicate_fields_are_rejected_instead_of_changing_results(self):
        original = json.dumps(record())
        cases = [original.replace('"status": "passed"', '"status": "failed", "status": "passed"'),
                 original[:-1] + ', "input_tokens": 100, "input_tokens": 0}',
                 original[:-1] + ', "metadata": {"x": 1, "x": 2}}']
        for content in cases:
            with self.subTest(content=content), self.assertRaises(WeaverError):
                self.read_text(content)

    def test_nonfinite_and_unrepresentable_numbers_are_rejected(self):
        prefix = json.dumps(record())[:-1]
        cases = [prefix + ', "billed_cost_usd": ' + value + '}'
                 for value in ('NaN', 'Infinity', '-Infinity', '1e999', '9' * 5000)]
        cases.extend(json.dumps(record(**{key: 10**400}))
                     for key in ('duration_seconds', 'billed_cost_usd', 'input_tokens', 'output_tokens', 'tool_calls'))
        for content in cases:
            with self.subTest(content=content[:150]), self.assertRaises(WeaverError):
                self.read_text(content)

    def test_parser_recursion_and_invalid_utf8_raise_weaver_error(self):
        with patch('agentweaver.report.json.loads', side_effect=RecursionError('NEVER-ECHO')):
            with self.assertRaises(WeaverError) as raised:
                self.read_text(json.dumps(record()))
        self.assertNotIn('NEVER-ECHO', str(raised.exception))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.jsonl'
            path.write_bytes(b'\xff\xfe')
            with self.assertRaises(WeaverError):
                read_runs(path)

    def test_aggregates_cannot_emit_nonfinite_numbers(self):
        cases = [dict(billed_cost_usd=1e308), dict(input_tokens=10**308, output_tokens=10**308),
                 dict(duration_seconds=1e308)]
        for metrics in cases:
            rows = [record(**metrics), dict(record(**metrics), run_id='2')]
            content = '\n'.join(json.dumps(row) for row in rows)
            with self.subTest(metrics=metrics), self.assertRaises(WeaverError):
                summarize(self.read_text(content))

    def test_comparison_percentages_cannot_emit_nonfinite_numbers(self):
        rows = [record(duration_seconds=1e-308),
                dict(record(duration_seconds=1e308), variant='optimized')]
        with self.assertRaises(WeaverError):
            compare_runs(rows, agent='kilo', source='synthetic')

    def test_mixed_integer_float_cost_overflow_raises_weaver_error(self):
        for costs in ((10**308, 10**308, 0.1), (10**308, 0.1, 10**308), (0.1, 10**308, 10**308)):
            content = '\n'.join(json.dumps(dict(record(billed_cost_usd=cost), run_id=str(index)))
                                for index, cost in enumerate(costs))
            rows = self.read_text(content)
            with self.subTest(costs=costs), self.assertRaisesRegex(WeaverError, 'aggregate exceeds'):
                summarize(rows)

    def test_unknown_metrics_and_ordinary_finite_values_are_preserved(self):
        rows = self.read_text(json.dumps(record(duration_seconds=2.0)))
        group = summarize(rows)['groups'][0]
        self.assertIsNone(group['total_tokens'])
        self.assertIsNone(group['total_billed_cost_usd'])
        self.assertEqual(group['median_duration_seconds'], 2.0)
        known = record(input_tokens=100, output_tokens=50, billed_cost_usd=0.02, duration_seconds=2.0)
        group = summarize(self.read_text(json.dumps(known)))['groups'][0]
        self.assertEqual(group['tokens_per_successful_task'], 150)
        self.assertEqual(group['cost_per_successful_task_usd'], 0.02)

    def test_cli_rejects_invalid_metrics_without_tracebacks_or_output(self):
        cases = [json.dumps(record(duration_seconds=10**400)),
                 '\n'.join(json.dumps(row) for row in
                           (record(billed_cost_usd=1e308), dict(record(billed_cost_usd=1e308), run_id='2'))),
                 '\n'.join(json.dumps(dict(record(billed_cost_usd=cost), run_id=str(index)))
                           for index, cost in enumerate((10**308, 10**308, 0.1)))]
        for content in cases:
            with self.subTest(content=content[:150]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic.jsonl'
                path.write_text(content, encoding='utf-8')
                destination = Path(directory) / 'report.json'
                code, output, error = invoke('report', '--input', str(path), '--format', 'json',
                                             '--output', str(destination))
                self.assertEqual(code, 2)
                self.assertEqual(output, '')
                self.assertNotIn('Traceback', error)
                self.assertFalse(destination.exists())

    def test_compare_cli_rejects_overflow_without_tracebacks_or_output(self):
        rows = [record(duration_seconds=1e-308),
                dict(record(duration_seconds=1e308), variant='optimized')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            destination = Path(directory) / 'compare.json'
            code, output, error = invoke('compare', '--input', str(path), '--agent', 'kilo', '--source', 'synthetic',
                                         '--output', str(destination))
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertNotIn('Traceback', error)
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
