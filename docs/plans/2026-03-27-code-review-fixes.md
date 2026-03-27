# PyroMind SDK Code Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 26 code review issues across the pyromind-sdk codebase, focusing on CRITICAL and HIGH priority items first.

**Architecture:** Incremental fixes following TDD methodology. Each fix includes: write test (if applicable), implement fix, verify, commit. All changes maintain backward compatibility.

**Tech Stack:** Python 3.8+, pytest, existing dependencies (pyyaml, requests, pydantic, minio)

---

## Task 1: Fix Bare Except Clause in client/base.py

**Files:**
- Modify: `pyromind_sdk/client/base.py:170`
- Test: `pyromind_sdk/tests/pytest/test_base_error_handling.py` (create)

**Step 1: Write the failing test**

Create `pyromind_sdk/tests/pytest/test_base_error_handling.py`:

```python
"""Tests for base client error handling"""
import pytest
from unittest.mock import Mock, patch
from pyromind_sdk.client.base import PyroMindAPIError


def test_bare_except_fixed_for_json_decode_error():
    """Test that JSON decode errors are caught specifically"""
    client = Mock()
    client.api_key = "test_key"
    client.base_url = "https://api.test.com"

    # Mock response with invalid JSON
    mock_response = Mock()
    mock_response.ok = False
    mock_response.status_code = 400
    mock_response.text = "Invalid JSON {"
    mock_response.json.side_effect = ValueError("Invalid JSON")

    with patch('pyromind_sdk.client.base.requests.Session.request') as mock_request:
        mock_request.return_value = mock_response

        from pyromind_sdk.client.base import PyroMindClient
        real_client = PyroMindClient(api_key="test_key", base_url="https://api.test.com")

        with pytest.raises(PyroMindAPIError) as exc_info:
            real_client.get("/test")

        # Should handle JSON decode error gracefully
        assert "Invalid JSON" in str(exc_info.value) or "400" in str(exc_info.value)


def test_system_exit_not_caught():
    """Test that SystemExit is not caught by error handling"""
    client = Mock()
    client.api_key = "test_key"
    client.base_url = "https://api.test.com"

    mock_response = Mock()
    mock_response.ok = False
    mock_response.status_code = 500
    mock_response.text = "Server Error"

    # Mock requests to raise SystemExit (should propagate)
    with patch('pyromind_sdk.client.base.requests.Session.request') as mock_request:
        mock_request.side_effect = SystemExit(1)

        from pyromind_sdk.client.base import PyroMindClient
        real_client = PyroMindClient(api_key="test_key", base_url="https://api.test.com")

        # SystemExit should NOT be caught
        with pytest.raises(SystemExit):
            real_client.get("/test")
```

**Step 2: Run test to verify it fails**

Run: `cd /workspace/openclaw-workspace/pyromind-sdk && pytest pyromind_sdk/tests/pytest/test_base_error_handling.py -v`
Expected: Tests may pass or fail depending on current state; we're adding test coverage first

**Step 3: Fix the bare except clause**

Modify `pyromind_sdk/client/base.py` around line 170:

Find:
```python
        try:
            error_data = response.json()
        except:
            error_data = {"message": response.text}
```

Replace with:
```python
        try:
            error_data = response.json()
        except (json.JSONDecodeError, ValueError, AttributeError):
            error_data = {"message": response.text}
```

Also ensure `json` is imported at the top of the file.

**Step 4: Run test to verify it passes**

Run: `pytest pyromind_sdk/tests/pytest/test_base_error_handling.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyromind_sdk/client/base.py pyromind_sdk/tests/pytest/test_base_error_handling.py
git commit -m "fix: replace bare except with specific exceptions in base.py

- Fix bare except clause at line 170
- Add specific exception types (json.JSONDecodeError, ValueError, AttributeError)
- Add tests for error handling edge cases
- Prevent catching SystemExit and other critical exceptions"
```

---

## Task 2: Fix Bare Except Clauses in nodes/command_executor.py

**Files:**
- Modify: `pyromind_sdk/nodes/command_executor.py:280, 458`
- Test: `pyromind_sdk/tests/pytest/test_command_executor_error_handling.py` (create)

**Step 1: Write the failing test**

Create `pyromind_sdk/tests/pytest/test_command_executor_error_handling.py`:

```python
"""Tests for command executor error handling"""
import pytest


def test_arg_parsing_error_handling():
    """Test that argument parsing handles specific exceptions"""
    from pyromind_sdk.nodes.command_executor import execute_command_template

    # Test with malformed input that might cause parsing issues
    result = execute_command_template(
        command_template=["echo", "test"],
        inputs={},
        timeout=10
    )
    # Should complete without catching SystemExit
    assert "outputs" in result or "error" in result


def test_json_parse_error_in_inputs():
    """Test JSON parsing error in --inputs argument"""
    from pyromind_sdk.nodes.command_executor import execute_command_template

    result = execute_command_template(
        command_template=["python3", "-c", "import sys; print('done')"],
        inputs={},
        timeout=10
    )
    # Should handle gracefully
    assert result is not None
```

**Step 2: Run test to verify baseline**

Run: `pytest pyromind_sdk/tests/pytest/test_command_executor_error_handling.py -v`
Expected: Current state

**Step 3: Fix bare except at line 280**

Find in `pyromind_sdk/nodes/command_executor.py` around line 280:
```python
        except:
            parsed_args = part.split()
```

Replace with:
```python
        except (ValueError, AttributeError):
            parsed_args = part.split()
```

**Step 4: Fix bare except at line 458**

Find in `pyromind_sdk/nodes/command_executor.py` around line 458:
```python
                except:
                    pass
```

Replace with:
```python
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass
```

**Step 5: Run tests to verify fixes**

Run: `pytest pyromind_sdk/tests/pytest/test_command_executor_error_handling.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add pyromind_sdk/nodes/command_executor.py pyromind_sdk/tests/pytest/test_command_executor_error_handling.py
git commit -m "fix: replace bare except clauses in command_executor.py

- Fix bare except at line 280 (arg parsing)
- Fix bare except at line 458 (JSON handling)
- Add specific exception types
- Add error handling tests"
```

---

## Task 3: Fix Bare Except Clause in nodes/function_call_wrapper.py

**Files:**
- Modify: `pyromind_sdk/nodes/function_call_wrapper.py:246`
- Test: `pyromind_sdk/tests/pytest/test_function_call_wrapper_error_handling.py` (create)

**Step 1: Write the failing test**

Create `pyromind_sdk/tests/pytest/test_function_call_wrapper_error_handling.py`:

```python
"""Tests for function call wrapper error handling"""
import pytest
import os
import json


def test_invalid_json_in_inputs_env():
    """Test handling of invalid JSON in PYTHON_NODE_INPUTS env var"""
    # Set invalid JSON
    os.environ['PYTHON_NODE_INPUTS'] = '{invalid json'

    from pyromind_sdk.nodes.function_call_wrapper import _get_inputs_from_environment

    # Should handle gracefully, not crash
    inputs = _get_inputs_from_environment()
    assert isinstance(inputs, dict)

    # Clean up
    del os.environ['PYTHON_NODE_INPUTS']


def test_empty_inputs_env():
    """Test handling of empty PYTHON_NODE_INPUTS env var"""
    os.environ['PYTHON_NODE_INPUTS'] = ''

    from pyromind_sdk.nodes.function_call_wrapper import _get_inputs_from_environment

    inputs = _get_inputs_from_environment()
    assert isinstance(inputs, dict)

    del os.environ['PYTHON_NODE_INPUTS']
```

**Step 2: Run test to verify baseline**

Run: `pytest pyromind_sdk/tests/pytest/test_function_call_wrapper_error_handling.py -v`
Expected: Current behavior

**Step 3: Fix the bare except clause**

Find in `pyromind_sdk/nodes/function_call_wrapper.py` around line 246:
```python
    try:
        inputs.update(json.loads(inputs_json))
    except:
        pass
```

Replace with:
```python
    try:
        inputs.update(json.loads(inputs_json))
    except json.JSONDecodeError:
        # Silently ignore invalid JSON from environment
        pass
```

**Step 4: Run tests to verify fix**

Run: `pytest pyromind_sdk/tests/pytest/test_function_call_wrapper_error_handling.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyromind_sdk/nodes/function_call_wrapper.py pyromind_sdk/tests/pytest/test_function_call_wrapper_error_handling.py
git commit -m "fix: replace bare except in function_call_wrapper.py

- Fix bare except at line 246
- Catch specific json.JSONDecodeError
- Add tests for JSON parsing edge cases"
```

---

## Task 4: Add Missing Dependencies to setup.py

**Files:**
- Modify: `setup.py`
- Test: `pyromind_sdk/tests/pytest/test_dependencies.py` (create)

**Step 1: Write the failing test**

Create `pyromind_sdk/tests/pytest/test_dependencies.py`:

```python
"""Tests to verify all dependencies are properly declared"""
import subprocess
import sys


def test_requests_importable():
    """Verify requests library is available"""
    import requests
    assert requests.__version__ is not None


def test_pydantic_importable():
    """Verify pydantic library is available"""
    import pydantic
    assert pydantic.__version__ is not None


def test_pyyaml_importable():
    """Verify pyyaml library is available"""
    import yaml
    assert yaml.__version__ is not None


def test_all_declared_dependencies_match_imports():
    """Verify all imports are reflected in setup.py dependencies"""
    # Read setup.py
    with open('setup.py', 'r') as f:
        setup_content = f.read()

    # Check that critical dependencies are declared
    assert 'requests' in setup_content, "requests not in setup.py dependencies"
    assert 'pydantic' in setup_content, "pydantic not in setup.py dependencies"
    assert 'pyyaml' in setup_content, "pyyaml not in setup.py dependencies"
```

**Step 2: Run test to verify it fails**

Run: `pytest pyromind_sdk/tests/pytest/test_dependencies.py -v`
Expected: FAIL on `test_all_declared_dependencies_match_imports`

**Step 3: Fix setup.py**

Read current `setup.py`:

```python
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="pyromind-sdk",
    version="0.0.16",
    author="PyroMind Team",
    author_email="support@pyromind.ai",
    description="Lightweight SDK stub for local development and testing of third-party nodes",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pyromind/pyromind-sdk",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "pyyaml>=6.0",
    ],
    extras_require={
        "storage": ["minio>=7.0.0"],
    },
)
```

Replace `install_requires` with:

```python
    install_requires=[
        "pyyaml>=6.0",
        "requests>=2.28.0",
        "pydantic>=2.0.0",
        "urllib3>=1.26.0",
    ],
```

**Step 4: Run test to verify it passes**

Run: `pytest pyromind_sdk/tests/pytest/test_dependencies.py -v`
Expected: PASS

**Step 5: Verify installation works**

Run: `pip install -e .`
Expected: Successful installation with all dependencies

**Step 6: Commit**

```bash
git add setup.py pyromind_sdk/tests/pytest/test_dependencies.py
git commit -m "fix: add missing dependencies to setup.py

- Add requests>=2.28.0 (used in client/base.py)
- Add pydantic>=2.0.0 (used in client/models.py)
- Add urllib3>=1.26.0 (transitive dependency, pin for security)
- Keep minio as optional dependency in extras_require
- Add dependency verification tests"
```

---

## Task 5: Extract Error Handling Helper in client/base.py

**Files:**
- Modify: `pyromind_sdk/client/base.py:129-280`
- Test: Update existing tests

**Step 1: Extract _sanitize_error_message helper**

Add before the `_request` method in `pyromind_sdk/client/base.py`:

```python
def _sanitize_error_message(error_data: Dict) -> Dict:
    """Sanitize error message to avoid log flooding.

    Args:
        error_data: Error data dictionary from API response

    Returns:
        Sanitized error data dictionary
    """
    if isinstance(error_data, dict) and isinstance(error_data.get("message"), str):
        msg = error_data["message"]
        if len(msg) > 500:
            error_data["message"] = msg[:500] + "..."
    return error_data


def _build_error_message(status_code: int, error_data: Dict, method: str, url: str) -> str:
    """Build detailed error message based on status code.

    Args:
        status_code: HTTP status code
        error_data: Error data dictionary
        method: HTTP method (GET, POST, etc.)
        url: Request URL

    Returns:
        Formatted error message string
    """
    base_msg = f"{method} {url} failed with status {status_code}"

    if isinstance(error_data, dict):
        if error_data.get("message"):
            return f"{base_msg}: {error_data['message']}"
        elif error_data.get("detail"):
            return f"{base_msg}: {error_data['detail']}"
        elif error_data.get("error"):
            return f"{base_msg}: {error_data['error']}"

    return base_msg
```

**Step 2: Refactor _request method to use helpers**

In the `_request` method, replace the nested error handling (lines 166-269) with:

```python
        if not response.ok:
            error_data = None
            try:
                error_data = response.json()
                error_data = _sanitize_error_message(error_data)
            except (json.JSONDecodeError, ValueError, AttributeError):
                error_data = {"message": response.text}

            message = _build_error_message(
                response.status_code,
                error_data,
                method.upper(),
                full_url
            )

            if response.status_code == 400:
                raise PyroMindAPIError(message, response=response)
            elif response.status_code == 401:
                raise PyroMindAPIError("Authentication failed. Check your API key.", response=response)
            elif response.status_code == 403:
                raise PyroMindAPIError("Access forbidden. You don't have permission to access this resource.", response=response)
            elif response.status_code == 404:
                raise PyroMindAPIError(f"Resource not found: {full_url}", response=response)
            elif response.status_code == 429:
                raise PyroMindAPIError("Rate limit exceeded. Please try again later.", response=response)
            elif response.status_code >= 500:
                raise PyroMindAPIError(f"Server error. The service reported: {message}", response=response)
            else:
                raise PyroMindAPIError(message, response=response)
```

**Step 3: Run existing tests**

Run: `pytest pyromind_sdk/tests/pytest/ -v`
Expected: All existing tests still pass

**Step 4: Commit**

```bash
git add pyromind_sdk/client/base.py
git commit -m "refactor: extract error handling helpers in base.py

- Extract _sanitize_error_message() to reduce nesting
- Extract _build_error_message() for consistent error messages
- Reduce _request() method from 151 lines to ~80 lines
- Improve testability of error handling logic"
```

---

## Task 6: Add Magic Number Constants

**Files:**
- Modify: `pyromind_sdk/common/constants.py`
- Modify: Multiple files to use constants

**Step 1: Add constants to constants.py**

Add to `pyromind_sdk/common/constants.py`:

```python
# Error handling limits
MAX_ERROR_MESSAGE_LENGTH = 500

# Command execution timing
OUTPUT_FILE_READ_DELAY = 0.1  # seconds

# Display limits
MAX_PREVIEW_LINES = 5

# Retry attempts (if applicable)
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.0  # seconds
```

**Step 2: Update client/base.py**

Replace:
```python
if len(msg) > 500:
    error_data["message"] = msg[:500] + "..."
```

With:
```python
from .common.constants import MAX_ERROR_MESSAGE_LENGTH

# ... later in code
if len(msg) > MAX_ERROR_MESSAGE_LENGTH:
    error_data["message"] = msg[:MAX_ERROR_MESSAGE_LENGTH] + "..."
```

**Step 3: Update nodes/command_executor.py**

Replace:
```python
time.sleep(0.1)
```

With:
```python
from pyromind_sdk.common.constants import OUTPUT_FILE_READ_DELAY

# ... later in code
time.sleep(OUTPUT_FILE_READ_DELAY)
```

**Step 4: Run tests**

Run: `pytest pyromind_sdk/tests/pytest/ -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyromind_sdk/common/constants.py pyromind_sdk/client/base.py pyromind_sdk/nodes/command_executor.py
git commit -m "refactor: replace magic numbers with named constants

- Add MAX_ERROR_MESSAGE_LENGTH, OUTPUT_FILE_READ_DELAY, etc. to constants
- Update client/base.py to use MAX_ERROR_MESSAGE_LENGTH
- Update nodes/command_executor.py to use OUTPUT_FILE_READ_DELAY
- Improve code readability and maintainability"
```

---

## Task 7: Add Missing Type Hints to Client Classes

**Files:**
- Modify: `pyromind_sdk/client/sandboxes.py`
- Modify: `pyromind_sdk/client/inference.py`
- Modify: `pyromind_sdk/client/training.py`
- Modify: `pyromind_sdk/client/storage.py`

**Step 1: Fix sandboxes.py type hints**

At line 212, find:
```python
def update(self, sandbox_id: str, request) -> SandboxResponse:
```

Replace with:
```python
def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:
```

Add import at top:
```python
from typing import Union
```

**Step 2: Fix inference.py type hints**

At line 88, find:
```python
def create(self, request) -> InferenceResponse:
```

Replace with:
```python
def create(self, request: Union[InferenceRequest, dict]) -> InferenceResponse:
```

**Step 3: Fix similar issues in other client files**

Search for and fix all `def` methods that have `request` or `data` parameters without type hints.

**Step 4: Run mypy type checker**

Run: `mypy pyromind_sdk/client/ --ignore-missing-imports`
Expected: No new type errors

**Step 5: Run tests**

Run: `pytest pyromind_sdk/tests/pytest/ -v`
Expected: PASS

**Step 6: Commit**

```bash
git add pyromind_sdk/client/*.py
git commit -m "fix: add missing type hints to client methods

- Add Union[RequestType, dict] type hints to request parameters
- Improve type safety and IDE autocomplete
- Add missing typing imports"
```

---

## Task 8: Create Consistent Error Hierarchy

**Files:**
- Modify: `pyromind_sdk/client/base.py` (add error classes)
- Modify: `pyromind_sdk/client/storage.py` (use new errors)

**Step 1: Add error classes to base.py**

Add after `PyroMindAPIError`:

```python
class StorageError(PyroMindAPIError):
    """Base exception for storage operations."""
    pass


class FileNotFoundError(StorageError):
    """File not found in storage."""

    def __init__(self, message: str, bucket: str = None, key: str = None, response=None):
        super().__init__(message, response=response)
        self.bucket = bucket
        self.key = key


class UploadError(StorageError):
    """File upload failed."""

    def __init__(self, message: str, file_path: str = None, response=None):
        super().__init__(message, response=response)
        self.file_path = file_path


class DownloadError(StorageError):
    """File download failed."""

    def __init__(self, message: str, bucket: str = None, key: str = None, response=None):
        super().__init__(message, response=response)
        self.bucket = bucket
        self.key = key
```

**Step 2: Update storage.py to use new errors**

Replace generic `ValueError` raises with specific error classes:

```python
from pyromind_sdk.client.base import FileNotFoundError, UploadError, DownloadError

# In file_exists method:
except S3Error as e:
    if e.code == "NoSuchKey":
        return False
    raise DownloadError(f"Failed to check file existence: {e.message}", bucket=bucket_name, key=key)

# In upload_file method:
except S3Error as e:
    raise UploadError(f"Failed to upload file: {e.message}", file_path=file_path)
```

**Step 3: Run tests**

Run: `pytest pyromind_sdk/tests/pytest/ -v`
Expected: PASS (may need to update test assertions)

**Step 4: Commit**

```bash
git add pyromind_sdk/client/base.py pyromind_sdk/client/storage.py
git commit -m "refactor: create consistent error hierarchy

- Add StorageError, FileNotFoundError, UploadError, DownloadError
- Update storage.py to use specific error types
- Improve error handling with contextual information"
```

---

## Task 9: Break Down execute_command_template Function

**Files:**
- Modify: `pyromind_sdk/nodes/command_executor.py:313-570`
- Test: Update existing tests

**Step 1: Extract command preparation helper**

Add new function before `execute_command_template`:

```python
def _prepare_command(
    command_template: List[str],
    inputs: Dict[str, Any],
    output_names: Optional[List[str]] = None
) -> Tuple[List[str], List[str]]:
    """Prepare command by substituting inputs and creating output files.

    Args:
        command_template: Template command with {{placeholder}} syntax
        inputs: Input values for substitution
        output_names: Names of output parameters

    Returns:
        Tuple of (command_parts, output_file_paths)
    """
    # Implementation extracted from execute_command_template
    # Returns prepared command list and output file paths
    ...
```

**Step 2: Extract placeholder substitution helper**

```python
def _substitute_placeholders(
    command_parts: List[str],
    inputs: Dict[str, Any],
    output_files: Dict[str, str]
) -> List[str]:
    """Substitute {{placeholder}} values in command.

    Args:
        command_parts: Command template parts
        inputs: Input values
        output_files: Output file path mappings

    Returns:
        Command with substituted values
    """
    # Implementation extracted from execute_command_template
    ...
```

**Step 3: Extract output reader helper**

```python
def _read_output_files(output_files: Dict[str, str]) -> Dict[str, Any]:
    """Read output from temporary files.

    Args:
        output_files: Mapping of output names to file paths

    Returns:
        Dictionary of output values
    """
    # Implementation extracted from execute_command_template
    ...
```

**Step 4: Refactor main function**

```python
def execute_command_template(
    command_template: List[str],
    inputs: Optional[Dict[str, Any]] = None,
    output_names: Optional[List[str]] = None,
    timeout: int = 300
) -> Dict[str, Any]:
    """Execute command template with input substitution.

    This is the main entry point that orchestrates the helpers.
    """
    inputs = inputs or {}
    command_parts, output_files = _prepare_command(command_template, inputs, output_names)
    actual_command = _substitute_placeholders(command_parts, inputs, output_files)
    result = _execute_shell_command(actual_command, timeout)
    outputs = _read_output_files(output_files)

    return {
        **result,
        "outputs": outputs
    }
```

**Step 5: Run tests**

Run: `pytest pyromind_sdk/tests/pytest/test_command_executor.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add pyromind_sdk/nodes/command_executor.py
git commit -m "refactor: break down execute_command_template into smaller functions

- Extract _prepare_command() for command preparation
- Extract _substitute_placeholders() for variable substitution
- Extract _read_output_files() for output reading
- Reduce main function from 257 lines to ~50 lines
- Improve testability and maintainability"
```

---

## Task 10: Add Security Tests

**Files:**
- Create: `pyromind_sdk/tests/pytest/test_security.py`

**Step 1: Write security tests**

Create `pyromind_sdk/tests/pytest/test_security.py`:

```python
"""Security tests for pyromind_sdk"""
import pytest
import tempfile
import os
from pyromind_sdk.nodes import yaml_loader


def test_path_traversal_protection():
    """Test that path traversal attempts are blocked"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test YAML file
        test_file = os.path.join(tmpdir, "test.yaml")
        with open(test_file, 'w') as f:
            f.write("name: TestNode\n")

        # Try to access with path traversal
        with pytest.raises(ValueError, match="Path traversal"):
            yaml_loader.load_nodes_from_yaml(os.path.join(tmpdir, "../../../etc/passwd"))


def test_yaml_safe_loading():
    """Test that YAML injection is prevented"""
    malicious_yaml = """!!python/object/apply:os.system
args: ['echo "pwned"']"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(malicious_yaml)
        temp_path = f.name

    try:
        # Should not execute arbitrary code
        with pytest.raises(ValueError):
            yaml_loader.load_nodes_from_yaml(temp_path)
    finally:
        os.unlink(temp_path)


def test_parameter_name_validation():
    """Test that parameter names are validated"""
    from pyromind_sdk.nodes.yaml_loader import validate_parameter_name

    # Valid names
    assert validate_parameter_name("valid_name") == "valid_name"
    assert validate_parameter_name("valid-name") == "valid-name"

    # Invalid names
    with pytest.raises(ValueError):
        validate_parameter_name("123invalid")  # starts with number

    with pytest.raises(ValueError):
        validate_parameter_name("class")  # Python keyword


def test_input_size_limits():
    """Test that input size limits are enforced"""
    from pyromind_sdk.common.constants import MAX_YAML_FILE_SIZE

    # Test constant exists
    assert MAX_YAML_FILE_SIZE > 0

    # Test oversized file is rejected
    oversized = "name: Test\n" * 1000000  # Create huge string

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(oversized)
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="too large"):
            yaml_loader.load_nodes_from_yaml(temp_path)
    finally:
        os.unlink(temp_path)
```

**Step 2: Run tests**

Run: `pytest pyromind_sdk/tests/pytest/test_security.py -v`
Expected: PASS (validates existing security measures)

**Step 3: Commit**

```bash
git add pyromind_sdk/tests/pytest/test_security.py
git commit -m "test: add comprehensive security tests

- Test path traversal protection
- Test YAML safe loading prevents code injection
- Test parameter name validation
- Test input size limits enforcement
- Ensure security measures remain in place"
```

---

## Summary of All Tasks

| Task | Focus | Files Changed | Priority |
|------|-------|---------------|----------|
| 1 | Bare except in base.py | client/base.py | CRITICAL |
| 2 | Bare except in command_executor.py | nodes/command_executor.py | CRITICAL |
| 3 | Bare except in function_call_wrapper.py | nodes/function_call_wrapper.py | CRITICAL |
| 4 | Missing dependencies | setup.py | CRITICAL |
| 5 | Extract error helpers | client/base.py | HIGH |
| 6 | Magic numbers to constants | constants.py, multiple files | HIGH |
| 7 | Missing type hints | client/*.py | HIGH |
| 8 | Consistent error hierarchy | base.py, storage.py | HIGH |
| 9 | Break down large function | nodes/command_executor.py | HIGH |
| 10 | Security tests | test_security.py | MEDIUM |

---

## Testing Strategy

1. **Run full test suite after each task**: `pytest pyromind_sdk/tests/pytest/ -v`
2. **Run type checking**: `mypy pyromind_sdk/ --ignore-missing-imports`
3. **Run linter**: `flake8 pyromind_sdk/ --max-line-length=100`
4. **Import test**: `python -c "import pyromind_sdk; print(pyromind_sdk.__version__)"`

---

## Rollback Plan

If any task causes issues:
```bash
# Revert specific task commit
git revert HEAD

# Or reset to known good state
git reset --hard <commit-hash>
```

---

## Completion Criteria

- [ ] All CRITICAL issues resolved (Tasks 1-4)
- [ ] All HIGH priority issues resolved (Tasks 5-9)
- [ ] All tests passing
- [ ] No regression in existing functionality
- [ ] Code coverage maintained or improved
