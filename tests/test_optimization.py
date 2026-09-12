import unittest
from optimization import FailureAnalyzer
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

if __name__ == '__main__':
    unittest.main()
