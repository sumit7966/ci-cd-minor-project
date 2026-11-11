import unittest
from datetime import datetime, timedelta, timezone
from optimization.models import OptimizationRecommendation, PipelineRun
from optimization.recommender import OptimizationRecommender
from tests.optimization.fixtures import generate_synthetic_dataset

class TestOptimizationRecommender(unittest.TestCase):
    def setUp(self):
        self.dataset = generate_synthetic_dataset()

    def test_recommender_rules(self):
        recommender = OptimizationRecommender(self.dataset)
        recommendations = recommender.generate_recommendations()
        
        titles = [r.title for r in recommendations]
        
        # Rule 1: A stage consistently takes significantly longer than other stages
        # 'build' avg is ~275s. Total pipeline avg is ~417s. 275/417 = 65% > 40%.
        self.assertIn("Investigate Slow Stage", titles)
        slow_stage_rec = next(r for r in recommendations if r.title == "Investigate Slow Stage")
        self.assertEqual(slow_stage_rec.affected_stage, "build")
        
        # Rule 2: Independent tests consume a large portion of pipeline time
        # 'unit_tests' avg is ~150s. 150/417 = 35% > 30%.
        self.assertIn("Parallelize Independent Tests", titles)
        test_rec = next(r for r in recommendations if r.title == "Parallelize Independent Tests")
        self.assertEqual(test_rec.affected_stage, "unit_tests")
        
        # Rule 3: Dependency installation repeatedly consumes significant time
        # 'install_dependencies' avg is ~72.8s > 60s.
        self.assertIn("Implement Dependency Caching", titles)
        dep_rec = next(r for r in recommendations if r.title == "Implement Dependency Caching")
        self.assertEqual(dep_rec.affected_stage, "install_dependencies")
        
        # Rule 4: The same stage fails frequently.
        # 'unit_tests' failed 2 times out of 6 (33% > 20%, executions >= 3).
        self.assertIn("Investigate Stage Reliability", titles)
        fail_rec = next(r for r in recommendations if r.title == "Investigate Stage Reliability")
        self.assertEqual(fail_rec.affected_stage, "unit_tests")
        
        # Rule 5: Pipeline duration is increasing over time.
        # Successful runs durations: 320, 550, 580, 610. (Strictly increasing for the last 3: 550 < 580 < 610)
        self.assertIn("Review Recent Pipeline Changes", titles)
        trend_rec = next(r for r in recommendations if r.title == "Review Recent Pipeline Changes")
        self.assertIsNone(trend_rec.affected_stage)

    def test_recommender_no_runs(self):
        recommender = OptimizationRecommender([])
        recommendations = recommender.generate_recommendations()
        self.assertEqual(len(recommendations), 0)

    def test_to_dict_preserves_none_affected_stage(self):
        rec = OptimizationRecommendation(
            title="Review Recent Pipeline Changes",
            description="Overall pipeline duration is steadily increasing over time.",
            reason="The last 3 successful runs show an increasing duration trend.",
            affected_stage=None,
            priority="medium",
            recommendation_type="monitoring",
        )
        payload = rec.to_dict()
        self.assertIn("affected_stage", payload)
        self.assertIsNone(payload["affected_stage"])

    def test_rule4_fallback_without_stage_telemetry(self):
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        runs = [
            PipelineRun("1", "failure", 20.0, base, stages=[], failed_stage="test"),
            PipelineRun("2", "success", 25.0, base + timedelta(minutes=1), stages=[]),
            PipelineRun("3", "success", 22.0, base + timedelta(minutes=2), stages=[]),
            PipelineRun("4", "success", 21.0, base + timedelta(minutes=3), stages=[]),
        ]
        # 1/4 = 25.0% (> 20%) across 4 eligible runs (>= 3)
        recs = OptimizationRecommender(runs).generate_recommendations()
        reliability_recs = [r for r in recs if r.title == "Investigate Stage Reliability"]
        self.assertEqual(len(reliability_recs), 1)
        rec = reliability_recs[0]
        self.assertEqual(rec.affected_stage, "test")
        self.assertIn("pipeline-level recurrence rate", rec.reason.lower())
        self.assertIn("not a true stage failure rate", rec.reason.lower())
        self.assertNotIn("executed that stage", rec.reason.lower())

    def test_rule4_fallback_requires_min_runs_and_exceeds_threshold(self):
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        # Fewer than 3 eligible runs should not trigger fallback even with 100% failed_stage
        too_few_runs = [
            PipelineRun("1", "failure", 20.0, base, stages=[], failed_stage="build"),
            PipelineRun("2", "failure", 22.0, base + timedelta(minutes=1), stages=[], failed_stage="build"),
        ]
        self.assertEqual(
            [r for r in OptimizationRecommender(too_few_runs).generate_recommendations() if r.title == "Investigate Stage Reliability"],
            [],
        )

        # Exactly 20% (1/5) should not trigger because threshold is strictly > 20%
        at_threshold_runs = [
            PipelineRun("1", "failure", 20.0, base, stages=[], failed_stage="build"),
            PipelineRun("2", "success", 25.0, base + timedelta(minutes=1), stages=[]),
            PipelineRun("3", "success", 24.0, base + timedelta(minutes=2), stages=[]),
            PipelineRun("4", "success", 23.0, base + timedelta(minutes=3), stages=[]),
            PipelineRun("5", "success", 22.0, base + timedelta(minutes=4), stages=[]),
        ]
        self.assertEqual(
            [r for r in OptimizationRecommender(at_threshold_runs).generate_recommendations() if r.title == "Investigate Stage Reliability"],
            [],
        )

if __name__ == '__main__':
    unittest.main()
