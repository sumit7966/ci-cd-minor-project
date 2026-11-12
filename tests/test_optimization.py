from datetime import datetime, timezone
import unittest
from optimization import FailureAnalyzer, PipelineRun, StageRun
from tests.optimization.fixtures import generate_synthetic_dataset

class TestOptimizationEngine(unittest.TestCase):
    def setUp(self):
        self.dataset = generate_synthetic_dataset()

    def test_failure_analyzer(self):
        analyzer = FailureAnalyzer(self.dataset)
        
        # Test frequently failing stages
        failing_stages = analyzer.get_frequently_failing_stages()
        self.assertEqual(len(failing_stages), 1)
        self.assertEqual(failing_stages[0][0], "unit_tests")
        self.assertEqual(failing_stages[0][1], 2) # Fails in run-2 and run-4
        
        # Test common failure types
        types = analyzer.get_common_failure_types()
        self.assertIn("unit_tests", types)
        self.assertEqual(types["unit_tests"]["timeout"], 1)
        self.assertEqual(types["unit_tests"]["test_failure"], 1)
        
        # Test failure rate
        rate = analyzer.get_failure_rate()
        self.assertEqual(rate, 2/6)

    def test_stage_failure_metrics_excludes_skipped_and_incomplete(self):
        base = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("1", "failure", 20.0, base, stages=[
                StageRun("build", 10.0, "success", duration_available=True),
                StageRun("test", 5.0, "failure", "timeout", duration_available=True),
            ]),
            PipelineRun("2", "failure", 15.0, base, stages=[
                StageRun("build", 10.0, "success", duration_available=True),
                StageRun("test", 0.0, "skipped", duration_available=False, skipped_count=1),
            ]),
            PipelineRun("3", "failure", 12.0, base, stages=[
                StageRun("build", 5.0, "failure", "build_failure", duration_available=True),
                StageRun("test", 0.0, "skipped", duration_available=False, skipped_count=1),
            ]),
        ]
        analyzer = FailureAnalyzer(runs)
        stage_metrics = analyzer.get_stage_failure_metrics()

        # 'test' had 1 executed stage (failed) and 2 skipped -> stage failure rate = 1/1 = 100%
        self.assertEqual(stage_metrics["test"]["total_executions"], 1)
        self.assertEqual(stage_metrics["test"]["stage_failure_count"], 1)
        self.assertEqual(stage_metrics["test"]["stage_failure_rate"], 1.0)

        # 'build' had 3 executed stages and 1 failure -> stage failure rate = 1/3
        self.assertEqual(stage_metrics["build"]["total_executions"], 3)
        self.assertEqual(stage_metrics["build"]["stage_failure_count"], 1)
        self.assertAlmostEqual(stage_metrics["build"]["stage_failure_rate"], 1/3)

    def test_pipeline_failed_stage_recurrence_distinct_from_stage_failure_rate(self):
        base = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("1", "failure", 20.0, base, stages=[], failed_stage="deploy"),
            PipelineRun("2", "failure", 15.0, base, stages=[], failed_stage="deploy"),
            PipelineRun("3", "success", 12.0, base, stages=[]),
            PipelineRun("4", "success", 14.0, base, stages=[]),
        ]
        analyzer = FailureAnalyzer(runs)
        recurrence = analyzer.get_pipeline_failed_stage_recurrence()
        self.assertEqual(len(recurrence), 1)
        stage_name, count, rate = recurrence[0]
        self.assertEqual(stage_name, "deploy")
        self.assertEqual(count, 2)
        # 2 / 4 = 50.0% pipeline-level recurrence across all eligible runs
        self.assertEqual(rate, 0.5)

if __name__ == '__main__':
    unittest.main()
