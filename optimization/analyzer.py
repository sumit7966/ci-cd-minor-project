from typing import List, Dict, Tuple
from collections import Counter
from .models import PipelineRun

class FailureAnalyzer:
    """Analyzes pipeline runs to identify failure patterns."""
    
    def __init__(self, runs: List[PipelineRun]):
        self.runs = runs

    def get_frequently_failing_stages(self) -> List[Tuple[str, int]]:
        """Returns a list of stages sorted by their failure count."""
        failure_counts: Counter = Counter()
        for run in self.runs:
            if run.failed_stage:
                failure_counts[run.failed_stage] += 1
                
        return failure_counts.most_common()

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
