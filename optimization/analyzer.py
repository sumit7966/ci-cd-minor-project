from typing import Any, Counter as CounterType, Dict, List, Tuple
from collections import Counter
from .models import PipelineRun

class FailureAnalyzer:
    """Analyzes pipeline runs to identify failure patterns."""
    
    def __init__(self, runs: List[PipelineRun]):
        self.runs = runs

    def get_frequently_failing_stages(self) -> List[Tuple[str, int]]:
        """Returns a list of stages sorted by their pipeline-level failed_stage recurrence count."""
        failure_counts: Counter = Counter()
        for run in self.runs:
            if run.failed_stage:
                failure_counts[run.failed_stage] += 1
                
        return failure_counts.most_common()

    def get_stage_failure_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Returns stage-level failure metrics calculated from executed stages only.

        Excludes skipped and incomplete executions from failure-rate denominators.
        """
        stage_executions: CounterType[str] = Counter()
        stage_failures: CounterType[str] = Counter()

        for run in self.runs:
            for stage in run.stages:
                if not stage.is_executed:
                    continue
                stage_executions[stage.name] += 1
                if stage.status == 'failure':
                    stage_failures[stage.name] += 1

        metrics: Dict[str, Dict[str, Any]] = {}
        for stage_name, executions in stage_executions.items():
            failures = stage_failures[stage_name]
            metrics[stage_name] = {
                "total_executions": executions,
                "stage_failure_count": failures,
                "stage_failure_rate": (failures / executions) if executions > 0 else 0.0,
            }
        return metrics

    def get_pipeline_failed_stage_recurrence(self) -> List[Tuple[str, int, float]]:
        """Returns pipeline-level failed-stage recurrence counts and recurrence rates.

        Clearly distinguished from stage failure rates because per-stage execution
        counts are unavailable across runs without stage telemetry.
        """
        total_runs = len(self.runs)
        if total_runs == 0:
            return []
        frequently_failing = self.get_frequently_failing_stages()
        return [
            (stage, count, round(count / total_runs, 4))
            for stage, count in frequently_failing
        ]

    def get_common_failure_types(self) -> Dict[str, Counter]:
        """Returns failure types broken down by stage."""
        stage_failures: Dict[str, Counter] = {}
        
        for run in self.runs:
            for stage in run.stages:
                if stage.status == 'failure' and stage.failure_type:
                    if stage.name not in stage_failures:
                        stage_failures[stage.name] = Counter()
                    stage_failures[stage.name][stage.failure_type] += 1
                    
        return stage_failures

    def get_failure_rate(self) -> float:
        """Calculates the overall pipeline failure rate."""
        if not self.runs:
            return 0.0
            
        failed_runs = sum(1 for run in self.runs if run.status == 'failure')
        return failed_runs / len(self.runs)
