export const overviewData = {
  totalRuns: 1248,
  successRate: "87.5%",
  failureRate: "12.5%",
  avgDuration: "14m 32s"
};

export const pipelineHistory = [
  { id: "RUN-1042", branch: "main", status: "Success", duration: "12m 45s", failedStage: "-", timestamp: "2026-09-12 10:23:45" },
  { id: "RUN-1041", branch: "feature/auth-service", status: "Failed", duration: "08m 12s", failedStage: "Integration Tests", timestamp: "2026-09-12 09:14:22" },
  { id: "RUN-1040", branch: "main", status: "Success", duration: "14m 50s", failedStage: "-", timestamp: "2026-09-12 08:05:11" },
  { id: "RUN-1039", branch: "bugfix/login-crash", status: "Failed", duration: "03m 40s", failedStage: "Build", timestamp: "2026-09-11 18:42:30" },
  { id: "RUN-1038", branch: "main", status: "Success", duration: "13m 20s", failedStage: "-", timestamp: "2026-09-11 15:30:00" },
  { id: "RUN-1037", branch: "feature/dashboard", status: "Success", duration: "15m 10s", failedStage: "-", timestamp: "2026-09-11 11:20:15" },
];

export const failurePredictions = [
  { id: "RUN-1043", branch: "feature/payment-gateway", failureProbability: "82%", predictedStage: "E2E Tests", status: "High Risk" },
  { id: "RUN-1044", branch: "main", failureProbability: "14%", predictedStage: "None", status: "Low Risk" },
  { id: "RUN-1045", branch: "hotfix/db-index", failureProbability: "45%", predictedStage: "Database Migration", status: "Medium Risk" },
];

export const optimizationData = [
  { bottleneck: "E2E Tests", duration: "45m", frequency: "42%", recommendations: "Parallelize test runners, Mock external services", priority: "High" },
  { bottleneck: "Docker Build", duration: "12m", frequency: "15%", recommendations: "Use multi-stage builds, cache layers", priority: "Medium" },
  { bottleneck: "Integration Tests", duration: "18m", frequency: "28%", recommendations: "Optimize DB seeding, reduce wait times", priority: "High" },
];

export const pipelineDetails = {
  id: "RUN-1041",
  branch: "feature/auth-service",
  status: "Failed",
  triggeredBy: "lakshay-y",
  stages: [
    { name: "Checkout", status: "Success", duration: "12s" },
    { name: "Install Dependencies", status: "Success", duration: "1m 45s" },
    { name: "Lint & Format", status: "Success", duration: "45s" },
    { name: "Unit Tests", status: "Success", duration: "2m 10s" },
    { name: "Build", status: "Success", duration: "3m 20s" },
    { name: "Integration Tests", status: "Failed", duration: "N/A" },
  ]
};
