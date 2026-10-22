import unittest

from agentweaver.compare import compare_runs
from agentweaver.config import WeaverError
from agentweaver.report import summarize


def run(variant, status, rid="0"):
    return {"task_id":"t1", "run_id":rid, "agent":"kilo", "variant":variant,
            "source":"synthetic", "status":status, "input_tokens":100,
            "output_tokens":50, "billed_cost_usd":0.01, "duration_seconds":2.0}

class ReportTests(unittest.TestCase):
    def test_cost_per_success(self):
        result=summarize([run("baseline","passed"),run("baseline","failed","1")])["groups"][0]
        self.assertEqual(result["tokens_per_successful_task"],300)
        self.assertAlmostEqual(result["cost_per_successful_task_usd"],0.02)
    def test_missing_cost_unknown(self):
        record=run("baseline","passed")
        record.pop("billed_cost_usd")
        self.assertIsNone(summarize([record])["groups"][0]["cost_per_successful_task_usd"])
    def test_compare_requires_matching(self):
        with self.assertRaises(WeaverError):
            compare_runs([run("baseline","passed")],agent="kilo",source="synthetic")
    def test_compare_regressions(self):
        result=compare_runs([run("baseline","passed"),run("optimized","failed")],agent="kilo",source="synthetic")
        self.assertEqual(len(result["regressions"]),1)
