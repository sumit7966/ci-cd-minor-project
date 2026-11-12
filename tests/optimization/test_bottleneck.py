import unittest
from datetime import datetime, timedelta, timezone
from optimization.bottleneck import BottleneckDetector
from optimization.models import PipelineRun, StageRun
from tests.optimization.fixtures import generate_synthetic_dataset

class TestBottleneckDetector(unittest.TestCase):
    def setUp(self):
        self.dataset = generate_synthetic_dataset()

    def test_analyze_empty_dataset(self):
        detector = BottleneckDetector([])
        result = detector.analyze()
        self.assertEqual(result["average_pipeline_duration"], 0.0)
        self.assertEqual(result["stage_metrics"], {})
        self.assertIsNone(result["slowest_stage"])

    def test_analyze_with_data(self):
        detector = BottleneckDetector(self.dataset)
        result = detector.analyze()
        
        # Pipeline durations: 320, 225, 550, 222, 580, 610
        total_duration = sum([320, 225, 550, 222, 580, 610])
        expected_avg = total_duration / 6
        self.assertEqual(result["average_pipeline_duration"], expected_avg)
        
        self.assertIn("install_dependencies", result["stage_metrics"])
        self.assertIn("lint", result["stage_metrics"])
        self.assertIn("unit_tests", result["stage_metrics"])
        self.assertIn("build", result["stage_metrics"])
        
        # Check install_dependencies
        install_metrics = result["stage_metrics"]["install_dependencies"]
        self.assertEqual(install_metrics["total_executions"], 6)
        self.assertEqual(install_metrics["stage_failure_count"], 0)
        
        # Check unit_tests failure frequency
        unit_test_metrics = result["stage_metrics"]["unit_tests"]
        self.assertEqual(unit_test_metrics["total_executions"], 6)
        self.assertEqual(unit_test_metrics["stage_failure_count"], 2) # Fails in run-2 and run-4
        self.assertAlmostEqual(unit_test_metrics["stage_failure_rate"], 2/6)
        
        # Check build averages
        build_metrics = result["stage_metrics"]["build"]
        self.assertEqual(build_metrics["total_executions"], 4) # Success runs and last runs
        avg_build = sum([100, 320, 330, 350]) / 4
        self.assertEqual(build_metrics["average_stage_duration"], avg_build)
        
        # Slowest stage detection
        self.assertEqual(result["slowest_stage"], "build")
        
    def test_identify_potential_bottlenecks(self):
        detector = BottleneckDetector(self.dataset)
        result = detector.analyze()
        
        bottlenecks = result["potential_bottlenecks"]
        bottleneck_stages = [b["stage"] for b in bottlenecks]
        
        # Build is a bottleneck because average duration > 50% of avg pipeline duration
        self.assertIn("build", bottleneck_stages)
        # Unit tests fails > 20% of the time
        self.assertIn("unit_tests", bottleneck_stages)

    def test_analyze_runs_without_stage_telemetry(self):
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("101", "success", 30.0, base, stages=[]),
            PipelineRun("102", "failure", 50.0, base + timedelta(minutes=5), stages=[], failed_stage="test"),
        ]
        result = BottleneckDetector(runs).analyze()

        self.assertEqual(result["average_pipeline_duration"], 40.0)
        self.assertEqual(result["stage_metrics"], {})
        self.assertIsNone(result["slowest_stage"])
        self.assertEqual(result["potential_bottlenecks"], [])
        self.assertEqual(
            result["message"],
            "Stage-level duration breakdown is unavailable; only run-level pipeline duration and failed_stage telemetry are stored.",
        )
        self.assertEqual(result["telemetry_coverage"]["total_runs_analyzed"], 2)
        self.assertEqual(result["telemetry_coverage"]["runs_with_stage_telemetry"], 0)
        self.assertEqual(result["telemetry_coverage"]["runs_without_stage_telemetry"], 2)

    def test_skipped_and_incomplete_stages_excluded_from_duration_and_failure_rate(self):
        base = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("201", "success", 20.0, base, stages=[
                StageRun("build", 10.0, "success", duration_available=True),
                StageRun("test", 0.0, "skipped", duration_available=False, skipped_count=1),
            ]),
            PipelineRun("202", "failure", 25.0, base + timedelta(minutes=1), stages=[
                StageRun("build", 20.0, "failure", "build_failure", duration_available=True),
                StageRun("test", 0.0, "skipped", duration_available=False, skipped_count=1),
            ]),
            PipelineRun("203", "failure", 30.0, base + timedelta(minutes=2), stages=[
                StageRun("build", 0.0, "skipped", duration_available=False, skipped_count=1),
                StageRun("lint", 0.0, "incomplete", duration_available=False, incomplete_count=1),
            ]),
        ]
        result = BottleneckDetector(runs).analyze()
        stage_metrics = result["stage_metrics"]

        # 'build' had 2 completed executions (10s, 20s) and 1 skipped -> avg = 15.0s, max = 20.0s
        # Denominator is 2 executions, 1 failure -> failure_rate = 50.0% (not 1/3 = 33%)
        build_metrics = stage_metrics["build"]
        self.assertEqual(build_metrics["total_executions"], 2)
        self.assertEqual(build_metrics["valid_duration_count"], 2)
        self.assertEqual(build_metrics["average_stage_duration"], 15.0)
        self.assertEqual(build_metrics["maximum_stage_duration"], 20.0)
        self.assertEqual(build_metrics["stage_failure_count"], 1)
        self.assertEqual(build_metrics["stage_failure_rate"], 0.5)
        self.assertEqual(build_metrics["skipped_count"], 1)

        # 'test' had 0 executions, 2 skipped -> durations must be None (not 0.0)
        self.assertNotIn("test", stage_metrics)

        # 'lint' had 0 executions, 1 incomplete -> not in stage_metrics
        self.assertNotIn("lint", stage_metrics)

    def test_valid_zero_second_executed_stages_included_as_valid_observations(self):
        base = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("301", "success", 10.0, base, stages=[
                StageRun("setup", 0.0, "success", duration_available=True),
                StageRun("compile", 6.0, "success", duration_available=True),
            ]),
            PipelineRun("302", "success", 12.0, base + timedelta(minutes=1), stages=[
                StageRun("setup", 0.0, "success", duration_available=True),
                StageRun("compile", 8.0, "success", duration_available=True),
            ]),
        ]
        result = BottleneckDetector(runs).analyze()
        setup_metrics = result["stage_metrics"]["setup"]
        self.assertEqual(setup_metrics["total_executions"], 2)
        self.assertEqual(setup_metrics["valid_duration_count"], 2)
        self.assertEqual(setup_metrics["average_stage_duration"], 0.0)
        self.assertEqual(setup_metrics["maximum_stage_duration"], 0.0)

        compile_metrics = result["stage_metrics"]["compile"]
        self.assertEqual(compile_metrics["average_stage_duration"], 7.0)
        # compile is slowest stage (7.0 > 0.0)
        self.assertEqual(result["slowest_stage"], "compile")

    def test_telemetry_coverage_with_mixed_runs(self):
        base = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("401", "success", 10.0, base, stages=[
                StageRun("build", 4.0, "success", duration_available=True),
            ], has_stage_telemetry=True),
            PipelineRun("402", "success", 15.0, base + timedelta(minutes=1), stages=[], has_stage_telemetry=False),
            PipelineRun("403", "failure", 20.0, base + timedelta(minutes=2), stages=[], has_stage_telemetry=False, failed_stage="test"),
        ]
        result = BottleneckDetector(runs).analyze()
        cov = result["telemetry_coverage"]
        self.assertEqual(cov["total_runs_analyzed"], 3)
        self.assertEqual(cov["runs_with_stage_telemetry"], 1)
        self.assertEqual(cov["runs_without_stage_telemetry"], 2)
        self.assertEqual(cov["stage_executions_analyzed"], 1)

if __name__ == '__main__':
    unittest.main()
