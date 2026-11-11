from typing import Any, Dict, List
from .models import PipelineRun, OptimizationRecommendation
from .analyzer import FailureAnalyzer
from .bottleneck import BottleneckDetector

class OptimizationRecommender:
    """Generates recommendations based on pipeline analysis."""
    
    def __init__(self, runs: List[PipelineRun]):
        self.runs = runs
        self.analyzer = FailureAnalyzer(runs)
        self.detector = BottleneckDetector(runs)
        
    def generate_recommendations(self) -> List[OptimizationRecommendation]:
        """Runs all analysis and generates a list of actionable recommendations."""
        recommendations = []
        if not self.runs:
            return recommendations
            
        analysis = self.detector.analyze()
        stage_metrics = analysis.get("stage_metrics", {})
        avg_pipeline_duration = analysis.get("average_pipeline_duration", 0.0)
        
        # Rule 1: A stage consistently takes significantly longer than other stages
        # Rule 2: Independent tests consume a large portion of pipeline time.
        # Rule 3: Dependency installation repeatedly consumes significant time.
        for stage, metrics in stage_metrics.items():
            avg_duration = metrics["average_stage_duration"]
            
            # Rule 1
            if avg_duration > (avg_pipeline_duration * 0.4) and avg_pipeline_duration > 0:
                # Except tests and dependencies which have their own rules
                if "test" not in stage.lower() and "depend" not in stage.lower() and "install" not in stage.lower():
                    recommendations.append(OptimizationRecommendation(
                        title="Investigate Slow Stage",
                        description=f"The '{stage}' stage consumes a significant portion of pipeline time.",
                        reason=f"Average duration ({avg_duration:.1f}s) is > 40% of the total pipeline duration.",
                        affected_stage=stage,
                        priority="high",
                        recommendation_type="performance"
                    ))
            
            # Rule 2
            if "test" in stage.lower() and avg_duration > (avg_pipeline_duration * 0.3) and avg_pipeline_duration > 0:
                recommendations.append(OptimizationRecommendation(
                    title="Parallelize Independent Tests",
                    description=f"The '{stage}' stage takes a long time. Consider splitting tests into parallel jobs.",
                    reason=f"Test stage duration ({avg_duration:.1f}s) is > 30% of total pipeline duration.",
                    affected_stage=stage,
                    priority="medium",
                    recommendation_type="architecture"
                ))
                
            # Rule 3
            if ("depend" in stage.lower() or "install" in stage.lower()) and avg_duration > 60.0:
                recommendations.append(OptimizationRecommendation(
                    title="Implement Dependency Caching",
                    description=f"The '{stage}' stage frequently takes significant time. Cache dependencies to speed up subsequent runs.",
                    reason=f"Dependency stage takes {avg_duration:.1f}s on average (threshold > 60s).",
                    affected_stage=stage,
                    priority="medium",
                    recommendation_type="caching"
                ))

        # Rule 4: The same stage fails frequently.
        self._add_reliability_recommendations(recommendations, stage_metrics)

        # Rule 5: Pipeline duration is increasing over time.
        if len(self.runs) >= 3:
            sorted_runs = sorted(self.runs, key=lambda r: r.timestamp)
            durations = [r.pipeline_duration for r in sorted_runs if r.status == 'success']
            if len(durations) >= 3:
                # Check if strictly increasing for the last 3 successful runs
                if durations[-1] > durations[-2] > durations[-3]:
                    recommendations.append(OptimizationRecommendation(
                        title="Review Recent Pipeline Changes",
                        description="Overall pipeline duration is steadily increasing over time.",
                        reason="The last 3 successful runs show an increasing duration trend.",
                        affected_stage=None,
                        priority="medium",
                        recommendation_type="monitoring"
                    ))
                    
        return recommendations

    def _add_reliability_recommendations(
        self,
        recommendations: List[OptimizationRecommendation],
        stage_metrics: Dict[str, Any],
    ) -> None:
        """Add Rule 4 reliability recommendations using stage counts or a run-level fallback."""
        total_runs = len(self.runs)
        failing_stages = self.analyzer.get_frequently_failing_stages()

        for stage, count in failing_stages:
            if stage_metrics:
                total_execs = stage_metrics.get(stage, {}).get("total_executions", 0)
                failure_rate = (count / total_execs) if total_execs > 0 else 0.0
                if failure_rate > 0.2 and total_execs >= 3:
                    recommendations.append(OptimizationRecommendation(
                        title="Investigate Stage Reliability",
                        description=f"The '{stage}' stage is failing frequently.",
                        reason=f"Stage has a failure rate of {failure_rate*100:.1f}% ({count} failures in {total_execs} runs).",
                        affected_stage=stage,
                        priority="high",
                        recommendation_type="reliability"
                    ))
            elif total_runs >= 3:
                recurrence_rate = count / total_runs
                if recurrence_rate > 0.2:
                    recommendations.append(OptimizationRecommendation(
                        title="Investigate Stage Reliability",
                        description=f"The '{stage}' stage repeatedly appears as the pipeline failed stage.",
                        reason=(
                            f"Pipeline-level failed-stage recurrence rate is {recurrence_rate * 100:.1f}% "
                            f"({count} failed runs attributed to '{stage}' across {total_runs} eligible runs; "
                            f"this is a pipeline-level recurrence rate, not a true stage failure rate because "
                            f"per-stage execution counts are unavailable)."
                        ),
                        affected_stage=stage,
                        priority="high",
                        recommendation_type="reliability"
                    ))
