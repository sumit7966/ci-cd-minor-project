"""Vercel Serverless Function entrypoint for CI/CD Failure Prediction & Optimization API."""

import os
import sys
from pathlib import Path

# Ensure the repository root is on sys.path so backend, optimization, and ml modules resolve cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Export the existing FastAPI application
from backend.app.main import app

