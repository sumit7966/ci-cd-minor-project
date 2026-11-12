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
                "message": "Empty dataset",
                "telemetry_coverage": {
                    "total_runs_analyzed": 0,
                    "runs_with_stage_telemetry": 0,
                    "runs_without_stage_telemetry": 0,
                    "stage_executions_analyzed": 0,
                },
            }
            
        total_pipeline_duration = 0.0
        runs_with_telemetry = 0
        
        stage_durations: Dict[str, List[float]] = defaultdict(list)
        stage_failures: Dict[str, int] = defaultdict(int)
        stage_executions: Dict[str, int] = defaultdict(int)
        stage_skipped: Dict[str, int] = defaultdict(int)
        stage_incomplete: Dict[str, int] = defaultdict(int)
        
        for run in self.runs:
            total_pipeline_duration += run.pipeline_duration
            if run.has_stage_telemetry or bool(run.stages):
                runs_with_telemetry += 1

            for stage in run.stages:
                stage_name = stage.name
                # Exclude skipped and incomplete stages from execution counts and duration pools
                if stage.status == 'skipped':
                    stage_skipped[stage_name] += (stage.skipped_count or 1)
                    continue
                if stage.status in ('incomplete', 'queued', 'pending', 'in_progress'):
                    stage_incomplete[stage_name] += (stage.incomplete_count or 1)
                    continue

                # Completed executions (including fast 0.0s executions and fast failures)
                stage_executions[stage_name] += 1
                if stage.duration_available and stage.duration is not None:
                    stage_durations[stage_name].append(stage.duration)
                
                if stage.status == 'failure':
                    stage_failures[stage_name] += 1

                if stage.skipped_count > 0:
                    stage_skipped[stage_name] += stage.skipped_count
                if stage.incomplete_count > 0:
                    stage_incomplete[stage_name] += stage.incomplete_count

        avg_pipeline_duration = total_pipeline_duration / len(self.runs)
        
        stage_metrics: Dict[str, Dict[str, Any]] = {}
        slowest_stage_name: Optional[str] = None
        max_duration_overall = -1.0
        
        for stage, durations in stage_durations.items():
            executions = stage_executions[stage]
            failures = stage_failures[stage]
            
            avg_duration = (sum(durations) / len(durations)) if durations else None
            max_duration = max(durations) if durations else None
            failure_rate = (failures / executions) if executions > 0 else 0.0
            
            stage_metrics[stage] = {
                "average_stage_duration": avg_duration,
                "maximum_stage_duration": max_duration,
                "stage_failure_count": failures,
                "stage_failure_rate": failure_rate,
                "total_executions": executions,
                "valid_duration_count": len(durations),
                "skipped_count": stage_skipped[stage],
                "incomplete_count": stage_incomplete[stage],
            }
            
            if avg_duration is not None and avg_duration > max_duration_overall:
                max_duration_overall = avg_duration
                slowest_stage_name = stage

        # Also register stages that had executions but no valid durations
        for stage, executions in stage_executions.items():
            if stage not in stage_metrics:
                failures = stage_failures[stage]
                failure_rate = (failures / executions) if executions > 0 else 0.0
                stage_metrics[stage] = {
                    "average_stage_duration": None,
                    "maximum_stage_duration": None,
                    "stage_failure_count": failures,
                    "stage_failure_rate": failure_rate,
                    "total_executions": executions,
                    "valid_duration_count": 0,
                    "skipped_count": stage_skipped[stage],
                    "incomplete_count": stage_incomplete[stage],
                }
                
        # Identify bottlenecks based on transparent rules
        # Rule 1: A stage is a bottleneck if its average duration is more than 50% of the average pipeline duration.
        # Rule 2: A stage is a bottleneck if it fails more than 20% of the time and has been executed at least 3 times.
        potential_bottlenecks = []
        for stage, metrics in stage_metrics.items():
            reasons = []
            avg_dur = metrics["average_stage_duration"]
            if (
                avg_dur is not None
                and avg_pipeline_duration > 0
                and avg_dur > (avg_pipeline_duration * 0.5)
            ):
                reasons.append("Stage takes up more than 50% of the average pipeline duration.")
            
            if metrics["stage_failure_rate"] > 0.2 and metrics["total_executions"] >= 3:
                reasons.append("Stage has a high failure rate (>20%).")
                
            if reasons:
                potential_bottlenecks.append({
                    "stage": stage,
                    "reasons": reasons
                })
                
        telemetry_coverage = {
            "total_runs_analyzed": len(self.runs),
            "runs_with_stage_telemetry": runs_with_telemetry,
            "runs_without_stage_telemetry": len(self.runs) - runs_with_telemetry,
            "stage_executions_analyzed": sum(m["total_executions"] for m in stage_metrics.values()),
        }

        result: Dict[str, Any] = {
            "average_pipeline_duration": avg_pipeline_duration,
            "stage_metrics": stage_metrics,
            "slowest_stage": slowest_stage_name,
            "potential_bottlenecks": potential_bottlenecks,
            "telemetry_coverage": telemetry_coverage,
        }
        if not stage_metrics:
            result["message"] = (
                "Stage-level duration breakdown is unavailable; only run-level "
                "pipeline duration and failed_stage telemetry are stored."
            )
        return result
        
    def identify_slow_stages(self, threshold_seconds: float = 300.0) -> List[str]:
        """Legacy compatibility wrapper for older tests/recommender."""
        analysis = self.analyze()
        return [
            stage for stage, metrics in analysis.get("stage_metrics", {}).items()
            if metrics.get("average_stage_duration") is not None
            and metrics["average_stage_duration"] > threshold_seconds
        ]
