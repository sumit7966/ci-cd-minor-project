import unittest
from optimization.bottleneck import BottleneckDetector
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

if __name__ == '__main__':
    unittest.main()
