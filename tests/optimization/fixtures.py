from datetime import datetime, timedelta
from optimization.models import PipelineRun, StageRun

# Base timestamp to ensure consistent testing
base_time = datetime(2023, 10, 1, 10, 0)

def generate_synthetic_dataset():
    """
    Returns a realistic synthetic dataset for testing optimization engine.
    Contains successful/failed runs, varying durations, slow stages, and flaky stages.
    """
    return [
        # Run 1: Normal successful run
        PipelineRun(
            run_id="run-1",
            status="success",
            pipeline_duration=320.0,
            timestamp=base_time,
            stages=[
                StageRun("install_dependencies", 60.0, "success"),
                StageRun("lint", 10.0, "success"),
                StageRun("unit_tests", 150.0, "success"),
                StageRun("build", 100.0, "success")
            ]
        ),
        # Run 2: Failed due to flaky tests
        PipelineRun(
            run_id="run-2",
            status="failure",
            pipeline_duration=225.0,
            timestamp=base_time + timedelta(hours=1),
            stages=[
                StageRun("install_dependencies", 65.0, "success"),
                StageRun("lint", 10.0, "success"),
                StageRun("unit_tests", 150.0, "failure", "timeout")
            ],
            failed_stage="unit_tests"
        ),
        # Run 3: Slower run with a very slow build stage (Bottleneck)
        PipelineRun(
            run_id="run-3",
            status="success",
            pipeline_duration=550.0,
            timestamp=base_time + timedelta(hours=2),
            stages=[
                StageRun("install_dependencies", 70.0, "success"),
                StageRun("lint", 12.0, "success"),
                StageRun("unit_tests", 148.0, "success"),
                StageRun("build", 320.0, "success") # Very slow!
            ]
        ),
        # Run 4: Failed again due to flaky tests
        PipelineRun(
            run_id="run-4",
            status="failure",
            pipeline_duration=222.0,
            timestamp=base_time + timedelta(hours=3),
            stages=[
                StageRun("install_dependencies", 62.0, "success"),
                StageRun("lint", 10.0, "success"),
                StageRun("unit_tests", 150.0, "failure", "test_failure")
            ],
            failed_stage="unit_tests"
        ),
        # Run 5: Successful run, even slower (trend increasing)
        PipelineRun(
            run_id="run-5",
            status="success",
            pipeline_duration=580.0,
            timestamp=base_time + timedelta(hours=4),
            stages=[
                StageRun("install_dependencies", 85.0, "success"),
                StageRun("lint", 15.0, "success"),
                StageRun("unit_tests", 150.0, "success"),
                StageRun("build", 330.0, "success")
            ]
        ),
        # Run 6: Successful run, longest duration (trend increasing)
        PipelineRun(
            run_id="run-6",
            status="success",
            pipeline_duration=610.0,
            timestamp=base_time + timedelta(hours=5),
            stages=[
                StageRun("install_dependencies", 95.0, "success"),
                StageRun("lint", 10.0, "success"),
                StageRun("unit_tests", 155.0, "success"),
                StageRun("build", 350.0, "success")
            ]
        )
    ]
