import unittest
import sys
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from optimization.api import app
except ImportError:
    # If fastapi is not installed in the environment, skip tests rather than failing imports
    TestClient = None

class TestOptimizationAPI(unittest.TestCase):
    def setUp(self):
        if TestClient is None:
            self.skipTest("fastapi or httpx is not installed")
        self.client = TestClient(app)

    def test_get_bottlenecks(self):
        response = self.client.get("/api/bottlenecks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("average_pipeline_duration", data)
        self.assertIn("stage_metrics", data)
        self.assertIn("potential_bottlenecks", data)

    def test_get_recommendations(self):
        response = self.client.get("/api/recommendations")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        if len(data) > 0:
            rec = data[0]
            self.assertIn("title", rec)
            self.assertIn("description", rec)
            self.assertIn("reason", rec)
            self.assertIn("affected_stage", rec)
            self.assertIn("priority", rec)
            self.assertIn("recommendation_type", rec)

    def test_get_optimization_summary(self):
        response = self.client.get("/api/optimization")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("bottlenecks", data)
        self.assertIn("recommendations", data)
        
    @patch('optimization.api.get_pipeline_data')
    def test_api_empty_data_handling(self, mock_get_data):
        # Simulate empty dataset
        mock_get_data.return_value = []
        
        res_bottlenecks = self.client.get("/api/bottlenecks")
        self.assertEqual(res_bottlenecks.status_code, 200)
        self.assertEqual(res_bottlenecks.json()["average_pipeline_duration"], 0.0)
        
        res_recs = self.client.get("/api/recommendations")
        self.assertEqual(res_recs.status_code, 200)
        self.assertEqual(res_recs.json(), [])
        
    @patch('optimization.api.get_pipeline_data')
    def test_api_error_handling(self, mock_get_data):
        # Simulate an unexpected error
        mock_get_data.side_effect = Exception("Database connection failed")
        
        response = self.client.get("/api/optimization")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Database connection failed", response.json()["detail"])

if __name__ == '__main__':
    unittest.main()
