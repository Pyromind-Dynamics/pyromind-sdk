#!/usr/bin/env python3
"""
Unified Test Entry Point

Run all integration tests with a single command.

Usage:
    python test_all.py                    # Run all tests (standalone)
    python test_all.py --quick            # Run quick tests only
    pytest test_all.py -v                 # Run with pytest directly (PyCharm)
"""

import argparse
import os
import subprocess
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Set PYTHONPATH environment variable
os.environ["PYTHONPATH"] = str(project_root)

# Suppress deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)


# Test modules configuration
TEST_MODULES = [
    # Unit tests (fast)
    "test_yaml_nodes_pytest.py",
    "test_python_function_to_yaml_pytest.py",
    "test_python_function_to_yaml_cli_pytest.py",
    "test_workflow_converter.py",
    # Integration tests (slower)
    # "test_sandbox_integration.py",
    "test_jupyter_instance_integration.py",
    "test_inference_integration.py",
    "test_studio_example.py",
    "test_echomind_integration.py",
    "test_studio_nodes_flow.py",
    "test_studio_nodes_integration.py",
]

UNIT_TESTS = [
    "test_yaml_nodes_pytest.py",
    "test_python_function_to_yaml_pytest.py",
    "test_workflow_converter.py",
]

INTEGRATION_TESTS = [m for m in TEST_MODULES if m not in UNIT_TESTS]


PYTEST_CLASS_MAP = {
    "all":         "TestAllModules",
    "quick":       "TestQuick",
    "integration": "TestIntegration",
}


def get_test_file_path(module_name: str) -> Path:
    """Get absolute path to test module"""
    return Path(__file__).parent / module_name


# ============================================================================
# Pytest Test Classes (for PyCharm/pytest discovery)
# ============================================================================

class TestAllModules:
    """Run all test modules - used by pytest discovery"""

    @pytest.mark.unit
    @pytest.mark.parametrize("module", UNIT_TESTS)
    def test_unit_module(self, module):
        """Run unit test module"""
        test_file = get_test_file_path(module)
        assert test_file.exists(), f"Test file not found: {test_file}"
        exit_code = pytest.main([str(test_file), "-v", "-s", "--tb=short"])
        assert exit_code == 0, f"Tests failed in {module}"

    @pytest.mark.integration
    @pytest.mark.parametrize("module", INTEGRATION_TESTS)
    def test_integration_module(self, module):
        """Run integration test module"""
        test_file = get_test_file_path(module)
        assert test_file.exists(), f"Test file not found: {test_file}"
        exit_code = pytest.main([str(test_file), "-v", "-s", "--tb=short"])
        assert exit_code == 0, f"Tests failed in {module}"


class TestQuick:
    """Run quick unit tests only"""

    @pytest.mark.parametrize("module", UNIT_TESTS)
    def test_module(self, module):
        """Run unit test module"""
        test_file = get_test_file_path(module)
        assert test_file.exists(), f"Test file not found: {test_file}"
        exit_code = pytest.main([str(test_file), "-v", "-s", "--tb=short"])
        assert exit_code == 0, f"Tests failed in {module}"


class TestIntegration:
    """Run integration tests only"""

    @pytest.mark.integration
    @pytest.mark.parametrize("module", INTEGRATION_TESTS)
    def test_module(self, module):
        """Run integration test module"""
        test_file = get_test_file_path(module)
        assert test_file.exists(), f"Test file not found: {test_file}"
        exit_code = pytest.main([str(test_file), "-v", "-s", "--tb=short"])
        assert exit_code == 0, f"Tests failed in {module}"


# ============================================================================
# Standalone Script Runner
# ============================================================================

def run_module_subprocess(module: str) -> tuple[str, int]:
    """单个模块独立子进程（等价于单类的 main 执行方式）"""
    test_file = Path(__file__).parent / module
    if not test_file.exists():
        print(f"[SKIP] {module} (file not found)")
        return (module, 0)

    print(f"[START] {module}")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v", "-s", "--tb=short"],
        env=env,
    )
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"[{status}] {module} (exit={result.returncode})")
    return (module, result.returncode)


def run_tests_parallel(modules: list, max_workers: int = 4) -> int:
    """并行执行：每个模块启动独立 pytest 进程"""
    print(f"\n并行执行 {len(modules)} 个模块（max_workers={max_workers}）")
    print("-" * 60)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_module_subprocess, m): m for m in modules}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda x: x[0])
    print("\n" + "=" * 60)
    print("汇总:")
    failed = []
    for module, code in results:
        mark = "PASS" if code == 0 else "FAIL"
        print(f"  [{mark}] {module}")
        if code != 0:
            failed.append(module)

    if failed:
        print(f"\n失败: {', '.join(failed)}")
        return 1
    print("\n全部通过")
    return 0


def run_pytest_class(class_name: str) -> int:
    """通过 subprocess 单独跑指定 pytest 类（与原嵌套 pytest.main 行为等价）"""
    test_file = Path(__file__).resolve()
    print(f"\n执行 pytest 类: {class_name}")
    print("-" * 60)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         f"{test_file.name}::{class_name}", "-v", "-s", "--tb=short"],
        cwd=str(test_file.parent),
        env=env,
    )
    return result.returncode


def check_environment():
    """Check if required environment variables are set"""
    api_key = os.getenv("PYROMIND_API_KEY")
    base_url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")

    print("=" * 60)
    print("PyroMind SDK Integration Tests")
    print("=" * 60)
    print(f"Base URL: {base_url}")
    print(f"API Key: {'Set (' + api_key[:10] + '...)'  if api_key else 'NOT SET'}")
    print("=" * 60)

    if not api_key:
        print("\nWarning: PYROMIND_API_KEY not set. Integration tests will be skipped.")
        print("Set the environment variable to run integration tests:")
        print("  export PYROMIND_API_KEY=your-api-key")

    return api_key is not None


def run_tests(modules=None, verbose=True, extra_args=None):
    """Run pytest on specified test modules"""
    test_dir = Path(__file__).parent

    if modules is None:
        modules = TEST_MODULES

    pytest_args = [sys.executable, "-m", "pytest"]
    if verbose:
        pytest_args.append("-v")
    pytest_args.append("-s")

    if extra_args:
        pytest_args.extend(extra_args)

    for module in modules:
        test_file = test_dir / module
        if test_file.exists():
            pytest_args.append(str(test_file))
        else:
            print(f"Warning: Test file not found: {test_file}")

    print(f"\nRunning tests: {', '.join(modules)}")
    print("-" * 60)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    result = subprocess.run(pytest_args, env=env)
    return result.returncode


def main():
    """Main entry point for standalone script"""
    parser = argparse.ArgumentParser(
        description="PyroMind SDK Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_all.py                     Run all tests
  python test_all.py --quick             Run quick unit tests only
  python test_all.py --module echomind   Run specific module
  python test_all.py --list              List available test modules
        """
    )

    parser.add_argument("--quick", action="store_true",
                        help="Run quick unit tests only")
    parser.add_argument("--module", type=str,
                        help="Run specific test module (e.g., echomind, inference)")
    parser.add_argument("--list", action="store_true",
                        help="List available test modules")
    parser.add_argument("--integration", action="store_true",
                        help="Run integration tests only")
    parser.add_argument("--unit", action="store_true",
                        help="Run unit tests only")
    parser.add_argument(
        "--class",
        dest="pytest_class",
        choices=list(PYTEST_CLASS_MAP.keys()),
        help="按 pytest 类执行: all / quick / integration"
    )
    parser.add_argument("-x", "--exitfirst", action="store_true",
                        help="Exit on first failure")
    parser.add_argument("--parallel", action="store_true",
                        help="并行执行（每个模块独立子进程）")
    parser.add_argument("-j", "--jobs", type=int, default=4,
                        help="并行度，默认 4")
    parser.add_argument("-k", type=str,
                        help="Run tests matching expression")
    parser.add_argument("--tb", type=str, default="short",
                        help="Traceback print mode")

    args = parser.parse_args()

    if args.list:
        print("Available test modules:")
        for i, module in enumerate(TEST_MODULES, 1):
            module_type = "unit" if module in UNIT_TESTS else "integration"
            print(f"  {i}. {module} ({module_type})")
        return 0

    has_api_key = check_environment()

    modules = None
    if args.quick:
        modules = UNIT_TESTS
    elif args.module:
        module_name = args.module.lower()
        matches = [m for m in TEST_MODULES if module_name in m.lower()]
        if matches:
            modules = matches
        else:
            print(f"Error: No test module matching '{args.module}'")
            return 1
    elif args.integration:
        modules = INTEGRATION_TESTS
    elif args.unit:
        modules = UNIT_TESTS
    elif args.pytest_class:
        return run_pytest_class(PYTEST_CLASS_MAP[args.pytest_class])

    if not has_api_key and modules is None:
        modules = UNIT_TESTS
        print("\nNo API key, running unit tests only.\n")

    extra_args = []
    if args.exitfirst:
        extra_args.append("-x")
    if args.k:
        extra_args.extend(["-k", args.k])
    if args.tb:
        extra_args.extend(["--tb", args.tb])

    if args.parallel:
        target = modules if modules is not None else TEST_MODULES
        exit_code = run_tests_parallel(target, max_workers=args.jobs)
    else:
        exit_code = run_tests(modules=modules, extra_args=extra_args)

    print("\n" + "=" * 60)
    if exit_code == 0:
        print("All tests passed!")
    else:
        print(f"Tests finished with exit code: {exit_code}")
    print("=" * 60)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())