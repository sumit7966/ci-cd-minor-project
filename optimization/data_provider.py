from typing import List
from .models import PipelineRun

def get_pipeline_data() -> List[PipelineRun]:
    """
    Retrieves pipeline execution data.
    
    Currently returns a temporary synthetic dataset for testing the API.
    Later, this function will be updated to query Member 1's real database.
    """
    # Import locally to avoid requiring tests module in production eventually,
    # but for now we rely on it as a temporary fixture.
    try:
        from tests.optimization.fixtures import generate_synthetic_dataset
        return generate_synthetic_dataset()
    except ImportError:
        # Fallback if fixtures are unavailable
        return []
