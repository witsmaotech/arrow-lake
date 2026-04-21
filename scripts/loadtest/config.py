"""Load test configuration."""

# Target throughput
TARGET_QPS = 50

# Ramp-up phase
RAMP_UP_SECONDS = 30

# Test duration (after ramp-up)
TEST_DURATION_SECONDS = 120

# Think time between requests (seconds)
THINK_TIME_MIN = 0.5
THINK_TIME_MAX = 2.0

# Task weights
VECTOR_WEIGHT = 4
FTS_WEIGHT = 3
HYBRID_WEIGHT = 2
INGEST_WEIGHT = 1

# API configuration
API_BASE_URL = "http://localhost:8000"
API_KEY = ""  # Set via env var LOAD_TEST_API_KEY
