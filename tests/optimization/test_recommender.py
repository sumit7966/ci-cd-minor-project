import unittest
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

if __name__ == '__main__':
    unittest.main()
