from typing import List, Dict, Any, Optional
from collections import defaultdict
from .models import PipelineRun

class BottleneckDetector:
    """Detects performance bottlenecks in pipeline executions."""
    
    def __init__(self, runs: List[PipelineRun]):
        self.runs = runs
        
    def analyze(self) -> Dict[str, Any]:
        """
        Performs a full bottleneck analysis and returns a structured dictionary 
        that can be easily serialized to JSON.
        """
        if not self.runs:
            return {
                "average_pipeline_duration": 0.0,
                "stage_metrics": {},
                "slowest_stage": None,
                "potential_bottlenecks": [],
                "message": "Empty dataset"
            }
            
        total_pipeline_duration = 0.0
        
        stage_durations: Dict[str, List[float]] = defaultdict(list)
        stage_failures: Dict[str, int] = defaultdict(int)
        stage_executions: Dict[str, int] = defaultdict(int)
        
        for run in self.runs:
            total_pipeline_duration += run.pipeline_duration
            
            for stage in run.stages:
                stage_name = stage.name
                stage_executions[stage_name] += 1
                stage_durations[stage_name].append(stage.duration)
                
                if stage.status == 'failure':
                    stage_failures[stage_name] += 1

        avg_pipeline_duration = total_pipeline_duration / len(self.runs)
        
        stage_metrics: Dict[str, Dict[str, Any]] = {}
        slowest_stage_name: Optional[str] = None
        max_duration_overall = -1.0
        
        for stage, durations in stage_durations.items():
            executions = stage_executions[stage]
            failures = stage_failures[stage]
            
            avg_duration = sum(durations) / len(durations) if durations else 0.0
            max_duration = max(durations) if durations else 0.0
            failure_rate = (failures / executions) if executions > 0 else 0.0
            
            stage_metrics[stage] = {
                "average_stage_duration": avg_duration,
                "maximum_stage_duration": max_duration,
                "stage_failure_count": failures,
                "stage_failure_rate": failure_rate,
                "total_executions": executions
            }
            
            if avg_duration > max_duration_overall:
                max_duration_overall = avg_duration
                slowest_stage_name = stage
                
        # Identify bottlenecks based on transparent rules
        # Rule 1: A stage is a bottleneck if its average duration is more than 50% of the average pipeline duration.
        # Rule 2: A stage is a bottleneck if it fails more than 20% of the time and has been executed at least 3 times.
        potential_bottlenecks = []
        for stage, metrics in stage_metrics.items():
            reasons = []
            if metrics["average_stage_duration"] > (avg_pipeline_duration * 0.5) and avg_pipeline_duration > 0:
                reasons.append("Stage takes up more than 50% of the average pipeline duration.")
            
            if metrics["stage_failure_rate"] > 0.2 and metrics["total_executions"] >= 3:
                reasons.append("Stage has a high failure rate (>20%).")
                
            if reasons:
                potential_bottlenecks.append({
                    "stage": stage,
                    "reasons": reasons
                })
                
        return {
            "average_pipeline_duration": avg_pipeline_duration,
            "stage_metrics": stage_metrics,
            "slowest_stage": slowest_stage_name,
            "potential_bottlenecks": potential_bottlenecks
        }
        
    def identify_slow_stages(self, threshold_seconds: float = 300.0) -> List[str]:
        """Legacy compatibility wrapper for older tests/recommender."""
        analysis = self.analyze()
        return [
            stage for stage, metrics in analysis.get("stage_metrics", {}).items()
            if metrics["average_stage_duration"] > threshold_seconds
        ]
