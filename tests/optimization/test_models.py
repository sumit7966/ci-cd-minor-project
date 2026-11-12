"""Unit tests for optimization domain models and telemetry metadata compatibility."""

from datetime import datetime, timezone
import unittest

from optimization.models import PipelineRun, StageRun
from tests.optimization.fixtures import generate_synthetic_dataset


class TestOptimizationDomainModels(unittest.TestCase):
    """Validates backward-compatible constructors and telemetry metadata defaults."""

    def test_existing_stage_run_positional_and_keyword_constructors(self) -> None:
        s3 = StageRun("install_dependencies", 60.0, "success")
        self.assertEqual(s3.name, "install_dependencies")
        self.assertEqual(s3.duration, 60.0)
        self.assertEqual(s3.status, "success")
        self.assertIsNone(s3.failure_type)
        self.assertTrue(s3.duration_available)
        self.assertEqual(s3.skipped_count, 0)
        self.assertEqual(s3.incomplete_count, 0)
        self.assertTrue(s3.is_executed)

        s4 = StageRun("unit_tests", 150.0, "failure", "timeout")
        self.assertEqual(s4.failure_type, "timeout")
        self.assertTrue(s4.duration_available)
        self.assertTrue(s4.is_executed)

        sk = StageRun(name="lint", duration=10.0, status="success")
        self.assertEqual(sk.name, "lint")
        self.assertTrue(sk.is_executed)

    def test_existing_pipeline_run_constructors_and_defaults(self) -> None:
        ts = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

        p_pos = PipelineRun("run-101", "success", 32.5, ts)
        self.assertEqual(p_pos.run_id, "run-101")
        self.assertEqual(p_pos.status, "success")
        self.assertEqual(p_pos.pipeline_duration, 32.5)
        self.assertEqual(p_pos.timestamp, ts)
        self.assertEqual(p_pos.stages, [])
        self.assertIsNone(p_pos.failed_stage)
        self.assertFalse(p_pos.has_stage_telemetry)
        self.assertEqual(p_pos.job_count, 0)
        self.assertEqual(p_pos.step_count, 0)
        self.assertIsNone(p_pos.queue_duration_seconds)

        stage = StageRun("build", 12.0, "failure", "build_error")
        p_kw = PipelineRun(
            run_id="run-102",
            status="failure",
            pipeline_duration=15.0,
            timestamp=ts,
            stages=[stage],
            failed_stage="build",
        )
        self.assertEqual(len(p_kw.stages), 1)
        self.assertEqual(p_kw.failed_stage, "build")
        self.assertFalse(p_kw.has_stage_telemetry)
        self.assertEqual(p_kw.job_count, 0)
        self.assertEqual(p_kw.step_count, 0)
        self.assertIsNone(p_kw.queue_duration_seconds)

    def test_explicit_telemetry_metadata_values(self) -> None:
        ts = datetime(2026, 10, 11, 8, 51, 19, tzinfo=timezone.utc)
        stage = StageRun(
            name="dependency",
            duration=3.75,
            status="success",
            failure_type=None,
            duration_available=True,
            skipped_count=0,
            incomplete_count=0,
        )
        run = PipelineRun(
            run_id="38126273805",
            status="failure",
            pipeline_duration=10.0,
            timestamp=ts,
            stages=[stage],
            failed_stage="dependency",
            has_stage_telemetry=True,
            job_count=1,
            step_count=9,
            queue_duration_seconds=2.5,
        )
        self.assertTrue(run.has_stage_telemetry)
        self.assertEqual(run.job_count, 1)
        self.assertEqual(run.step_count, 9)
        self.assertEqual(run.queue_duration_seconds, 2.5)
        self.assertTrue(run.stages[0].is_executed)

    def test_zero_second_completed_executions_remain_executed(self) -> None:
        fast_success = StageRun("setup", 0.0, "success", duration_available=True)
        fast_failure = StageRun("build", 0.0, "failure", "build_failure", duration_available=True)

        self.assertEqual(fast_success.duration, 0.0)
        self.assertTrue(fast_success.duration_available)
        self.assertTrue(fast_success.is_executed)

        self.assertEqual(fast_failure.duration, 0.0)
        self.assertTrue(fast_failure.duration_available)
        self.assertTrue(fast_failure.is_executed)

    def test_skipped_and_incomplete_stages_are_not_treated_as_executed(self) -> None:
        skipped = StageRun(
            name="test",
            duration=0.0,
            status="skipped",
            duration_available=False,
            skipped_count=1,
        )
        skipped_default_flag = StageRun("test", 0.0, "skipped")
        incomplete = StageRun(
            name="build",
            duration=0.0,
            status="incomplete",
            duration_available=False,
            incomplete_count=1,
        )
        missing_timing = StageRun("deploy", 0.0, "success", duration_available=False)

        self.assertFalse(skipped.is_executed)
        self.assertEqual(skipped.skipped_count, 1)
        self.assertFalse(skipped_default_flag.is_executed)
        self.assertFalse(incomplete.is_executed)
        self.assertEqual(incomplete.incomplete_count, 1)
        self.assertFalse(missing_timing.is_executed)

    def test_backward_compatibility_with_synthetic_fixtures(self) -> None:
        dataset = generate_synthetic_dataset()
        self.assertEqual(len(dataset), 6)
        for run in dataset:
            self.assertFalse(run.has_stage_telemetry)
            self.assertEqual(run.job_count, 0)
            self.assertEqual(run.step_count, 0)
            self.assertIsNone(run.queue_duration_seconds)
            for stage in run.stages:
                self.assertTrue(stage.duration_available)
                self.assertEqual(stage.skipped_count, 0)
                self.assertEqual(stage.incomplete_count, 0)
                self.assertTrue(stage.is_executed)


if __name__ == "__main__":
    unittest.main()
