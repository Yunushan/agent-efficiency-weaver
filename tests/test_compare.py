import unittest
from agentweaver.compare import compare_runs
from agentweaver.config import WeaverError

def task(variant, status="passed", task_id="demo"):
    return {"task_id": task_id, "run_id": "1", "agent": "kilo", "variant": variant,
            "source": "synthetic", "status": status}

class CompareTests(unittest.TestCase):
    def test_matched(self):
        result = compare_runs([task("baseline"), task("optimized")], agent="kilo", source="synthetic")
        self.assertEqual(result["matched_attempts"], 1)
        self.assertEqual(result["success_rate_delta_pp"], 0)
    def test_mismatch_rejected(self):
        with self.assertRaises(WeaverError):
            compare_runs([task("baseline"), task("optimized", task_id="other")],
                         agent="kilo", source="synthetic")
