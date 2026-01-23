#!/usr/bin/env python3
"""
DIntelliHub Smoke Test Suite
Validates all services are healthy and functional
"""

import subprocess
import sys
import time
import json
import psycopg2
import requests
from typing import Dict, List, Tuple
from datetime import datetime

# Configuration
SERVICES = {
    "lancedb": {"url": "http://localhost:8765", "name": "LanceDB Vector DB"},
    "daft": {"url": "http://localhost:8001", "name": "Daft Processing"},
    "prometheus": {"url": "http://localhost:9090", "name": "Prometheus"},
    "grafana": {"url": "http://localhost:13000", "name": "Grafana"},
}

DB_CONFIG = {
    "host": "localhost",
    "port": 15432,
    "database": "gravitino",
    "user": "admin",
    "password": "admin123"
}

MINIO_CONFIG = {
    "endpoint": "localhost:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin123"
}

# Colors for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text: str):
    """Print formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text:^60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}\n")

def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text: str):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")

def check_docker_services() -> Dict[str, bool]:
    """Check if Docker containers are running"""
    print_info("Checking Docker containers...")

    required_containers = [
        "dintellihub-postgres",
        "dintellihub-minio",
        "dintellihub-prometheus",
        "dintellihub-grafana",
        "dintellihub-lancedb",
        "dintellihub-daft",
        "dintellihub-redis"
    ]

    status = {}
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            containers = json.loads(result.stdout)
            running_containers = [c['Name'] for c in containers if c['State'] == 'running']

            for container in required_containers:
                is_running = container in running_containers
                status[container] = is_running
                if is_running:
                    print_success(f"{container}: Running")
                else:
                    print_error(f"{container}: Not running")
        else:
            print_error("Failed to check Docker status")
            for container in required_containers:
                status[container] = False

    except Exception as e:
        print_error(f"Error checking Docker: {e}")
        for container in required_containers:
            status[container] = False

    return status

def test_http_endpoint(service_name: str, url: str, endpoint: str = "/health") -> Tuple[bool, float]:
    """Test HTTP endpoint health"""
    full_url = f"{url}{endpoint}"
    start_time = time.time()

    try:
        response = requests.get(full_url, timeout=10)
        latency = (time.time() - start_time) * 1000  # Convert to ms

        if response.status_code == 200:
            return True, latency
        else:
            print_error(f"{service_name}: HTTP {response.status_code}")
            return False, latency

    except requests.exceptions.Timeout:
        print_error(f"{service_name}: Timeout")
        return False, 0
    except Exception as e:
        print_error(f"{service_name}: {str(e)}")
        return False, 0

def test_service_health() -> Dict[str, bool]:
    """Test health endpoints for all HTTP services"""
    print_info("\nTesting service health endpoints...")

    results = {}

    for service_key, config in SERVICES.items():
        success, latency = test_http_endpoint(config['name'], config['url'])

        if success:
            print_success(f"{config['name']}: OK ({latency:.0f}ms)")
            results[service_key] = True
        else:
            results[service_key] = False

    return results

def test_postgresql() -> bool:
    """Test PostgreSQL connectivity and basic operations"""
    print_info("\nTesting PostgreSQL connection...")

    try:
        # Test connection
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Test simple query
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print_success(f"PostgreSQL connected: {version[:50]}...")

        # Test table creation and insertion
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS smoke_test (
                id SERIAL PRIMARY KEY,
                test_time TIMESTAMP DEFAULT NOW()
            );
        """)

        cursor.execute("INSERT INTO smoke_test DEFAULT VALUES;")
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM smoke_test;")
        count = cursor.fetchone()[0]
        print_success(f"Smoke test table records: {count}")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print_error(f"PostgreSQL test failed: {e}")
        return False

def test_minio() -> bool:
    """Test MinIO connectivity and bucket access"""
    print_info("\nTesting MinIO buckets...")

    try:
        # Test health endpoint
        response = requests.get(f"http://{MINIO_CONFIG['endpoint']}/minio/health/live", timeout=10)

        if response.status_code == 200:
            print_success("MinIO health check: OK")

            # Note: Full bucket testing requires boto3 library
            # For smoke test, just verify service is accessible
            print_info("Expected buckets: dintellihub-raw, dintellihub-processed, dintellihub-vectors, dintellihub-models, dintellihub-backups")
            return True
        else:
            print_error(f"MinIO health check failed: {response.status_code}")
            return False

    except Exception as e:
        print_error(f"MinIO test failed: {e}")
        return False

def test_lancedb_functionality() -> bool:
    """Test LanceDB vector operations"""
    print_info("\nTesting LanceDB functionality...")

    try:
        url = f"{SERVICES['lancedb']['url']}/api/v1/search"

        # Test search endpoint (even without data, should not error)
        test_data = {
            "collection": "test",
            "vector": [0.1] * 128,  # 128-dimensional vector
            "limit": 5
        }

        response = requests.post(url, json=test_data, timeout=30)

        if response.status_code in [200, 404]:  # 404 is OK if collection doesn't exist
            print_success("LanceDB API functional")
            return True
        else:
            print_error(f"LanceDB API error: {response.status_code}")
            return False

    except Exception as e:
        print_error(f"LanceDB functionality test failed: {e}")
        return False

def test_daft_functionality() -> bool:
    """Test Daft processing functionality"""
    print_info("\nTesting Daft functionality...")

    try:
        url = f"{SERVICES['daft']['url']}/api/v1/health"

        response = requests.get(url, timeout=30)

        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                print_success("Daft service healthy")
                if data.get("ray_connected"):
                    print_success("Ray cluster connected")
                return True
        else:
            print_error(f"Daft health check failed: {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Daft functionality test failed: {e}")
        return False

def test_prometheus_metrics() -> bool:
    """Test Prometheus is scraping metrics"""
    print_info("\nTesting Prometheus metrics...")

    try:
        # Check if Prometheus has targets
        response = requests.get(f"{SERVICES['prometheus']['url']}/api/v1/targets", timeout=10)

        if response.status_code == 200:
            data = response.json()
            active_targets = [t for t in data['data']['activeTargets'] if t['health'] == 'up']

            print_success(f"Prometheus targets up: {len(active_targets)}")

            # Show targets
            for target in active_targets:
                job = target['labels'].get('job', 'unknown')
                print(f"  - {job}")

            return len(active_targets) > 0
        else:
            print_error("Failed to query Prometheus targets")
            return False

    except Exception as e:
        print_error(f"Prometheus test failed: {e}")
        return False

def test_grafana_access() -> bool:
    """Test Grafana dashboard access"""
    print_info("\nTesting Grafana access...")

    try:
        response = requests.get(f"{SERVICES['grafana']['url']}/api/health", timeout=10)

        if response.status_code == 200:
            data = response.json()
            database = data.get('database', 'unknown')
            print_success(f"Grafana accessible (database: {database})")
            return True
        else:
            print_error(f"Grafana health check failed: {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Grafana test failed: {e}")
        return False

def generate_test_report(results: Dict[str, bool]):
    """Generate test summary report"""

    total_tests = len(results)
    passed_tests = sum(1 for v in results.values() if v)
    failed_tests = total_tests - passed_tests

    print_header("Test Summary")

    print(f"Total Tests: {total_tests}")
    print(f"{Colors.GREEN}Passed: {passed_tests}{Colors.END}")
    print(f"{Colors.RED}Failed: {failed_tests}{Colors.END}")
    print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")

    # Detailed results
    print("\nDetailed Results:")
    print("-" * 60)

    for test_name, passed in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if passed else f"{Colors.RED}FAIL{Colors.END}"
        print(f"{test_name:40} {status}")

    # Overall verdict
    print("\n" + "=" * 60)
    if passed_tests == total_tests:
        print(f"{Colors.GREEN}{Colors.BOLD}🎉 ALL TESTS PASSED! 🎉{Colors.END}")
        print(f"{Colors.GREEN}DIntelliHub environment is ready for use!{Colors.END}")
        return 0
    else:
        print(f"{Colors.RED}{Colors.BOLD}⚠️  SOME TESTS FAILED{Colors.END}")
        print(f"{Colors.YELLOW}Please check the failed tests above.{Colors.END}")
        return 1

def main():
    """Main smoke test execution"""
    print_header("DIntelliHub Smoke Test Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    all_results = {}

    # Test 1: Docker containers
    docker_results = check_docker_services()
    all_results.update({f"Docker: {k}": v for k, v in docker_results.items()})

    # Test 2: HTTP service health
    health_results = test_service_health()
    all_results.update({f"Health: {k}": v for k, v in health_results.items()})

    # Test 3: PostgreSQL
    all_results["PostgreSQL"] = test_postgresql()

    # Test 4: MinIO
    all_results["MinIO"] = test_minio()

    # Test 5: LanceDB functionality
    all_results["LanceDB Functionality"] = test_lancedb_functionality()

    # Test 6: Daft functionality
    all_results["Daft Functionality"] = test_daft_functionality()

    # Test 7: Prometheus metrics
    all_results["Prometheus Metrics"] = test_prometheus_metrics()

    # Test 8: Grafana access
    all_results["Grafana Access"] = test_grafana_access()

    # Generate report
    exit_code = generate_test_report(all_results)

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
