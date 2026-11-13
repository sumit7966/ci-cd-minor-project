# Optimization Engine

This module is responsible for analyzing CI/CD pipeline execution data to identify bottlenecks, frequently failing stages, and possible optimization opportunities.

## Components

- **`models.py`**: Defines data structures like `PipelineRun` and `StageRun` to strongly type the data.
- **`analyzer.py`**: Uses `FailureAnalyzer` to identify failure patterns across historical pipeline runs.
- **`bottleneck.py`**: Uses `BottleneckDetector` to identify slow stages and track pipeline duration trends.
- **`recommender.py`**: Uses `OptimizationRecommender` to synthesize findings from the analyzer and bottleneck detector into actionable recommendations.

## Usage Example

```python
from optimization import OptimizationRecommender, PipelineRun, StageRun
from datetime import datetime

# Assuming data is populated by Member 1's CI/CD data collection module:
sample_runs = [
    PipelineRun(
        run_id="run-1",
        status="success",
        pipeline_duration=600.0,
        timestamp=datetime.now(),
        stages=[
            StageRun(name="build", duration=150.0, status="success"),
            StageRun(name="test", duration=450.0, status="success")
        ]
    )
]

recommender = OptimizationRecommender(sample_runs)
recommendations = recommender.generate_recommendations()

for rec in recommendations:
    print(rec.title, rec.description)
```
