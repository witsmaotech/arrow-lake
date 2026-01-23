#!/usr/bin/env python3
"""
DIntelliHub Quick Environment Validation
Fast health check for all services
"""

import subprocess
import sys
import requests
from typing import Dict, List, Tuple

def run_command(cmd: List[str]) -> Tuple[bool, str]:
    """Run command and return success status and output"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)

def check_port(host: str, port: int) -> bool:
    """Check if port is accessible"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def main():
    """Quick validation of DIntelliHub environment"""

    print("🔍 DIntelliHub Environment Quick Validation\n")

    services = [
        ("PostgreSQL", "localhost", 15432),
        ("MinIO API", "localhost", 9000),
        ("MinIO Console", "localhost", 9001),
        ("Redis", "localhost", 16379),
        ("Prometheus", "localhost", 9090),
        ("Grafana", "localhost", 13000),
        ("LanceDB", "localhost", 8765),
        ("Daft", "localhost", 8001),
    ]

    print("Port Accessibility:")
    all_ok = True
    for name, host, port in services:
        if check_port(host, port):
            print(f"  ✅ {name:20} ({port})")
        else:
            print(f"  ❌ {name:20} ({port})")
            all_ok = False

    # Check Docker containers
    print("\nDocker Containers:")
    success, output = run_command(["docker", "compose", "ps"])

    if success:
        lines = output.strip().split('\n')
        for line in lines[1:]:  # Skip header
            if 'running' in line.lower() or 'up' in line.lower():
                print(f"  ✅ {line}")
    else:
        print(f"  ❌ Failed to check containers")
        all_ok = False

    # Check HTTP endpoints
    print("\nHTTP Endpoints:")
    endpoints = [
        ("LanceDB Health", "http://localhost:8765/health"),
        ("Daft Health", "http://localhost:8001/health"),
        ("Prometheus", "http://localhost:9090"),
        ("Grafana", "http://localhost:13000"),
    ]

    for name, url in endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"  ✅ {name}")
            else:
                print(f"  ⚠️  {name} (HTTP {response.status_code})")
        except:
            print(f"  ❌ {name}")
            all_ok = False

    # Overall status
    print("\n" + "=" * 60)
    if all_ok:
        print("✅ All checks passed! Environment is healthy.")
        return 0
    else:
        print("⚠️  Some checks failed. Please review above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
