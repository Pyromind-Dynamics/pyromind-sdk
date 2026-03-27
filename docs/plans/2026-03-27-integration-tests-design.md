# Integration Tests Design for PyroMind SDK

**Date:** 2026-03-27
**Author:** Claude Code
**Status:** Approved

## Overview

Create real integration tests for all PyroMind SDK client modules using live API calls instead of mocks. Tests will use credentials stored in a local `.env` file (excluded from git).

## Environment Setup

### `.env` File

Create `.env` in the project root with credentials:

```env
# PyroMind API Credentials
PYROMIND_API_KEY=U47WH38PLANS3VOL04Q8
PYROMIND_STORAGE_SECRET_KEY=TXdcVvSIq1jEELwHdOwJ8rlhYw3hqr_Xf0ec4j9x9DK0KYc9277oCQ
PYROMIND_STORAGE_BUCKET=1000001019

# Optional: Override defaults
# PYROMIND_STORAGE_ENDPOINT=https://storage.pyromind.ai
# PYROMIND_BASE_URL=https://api.pyromind.ai/api/v1
```

### `.gitignore` Update

Add `.env` to prevent committing credentials:

```
# Environment variables
.env
.env.local
```

## Test File Structure

```
pyromind_sdk/tests/pytest/
├── conftest.py                      # Shared fixtures
├── test_storage_integration.py      # Storage client tests (NEW)
├── test_sandbox_integration.py      # Sandbox client tests (UPDATE)
├── test_inference_integration.py    # Inference client tests (UPDATE)
├── test_training_integration.py     # Training client tests (UPDATE)
├── test_instance_integration.py     # Instance client tests (UPDATE)
└── test_workflow_integration.py     # Workflow tests (NEW)
```

## Shared Fixtures (`conftest.py`)

```python
"""Shared fixtures for all integration tests"""

import os
import pytest
from pyromind_sdk import PyroMindAPIClient

@pytest.fixture(scope="session")
def api_key():
    """Get API key from environment variable"""
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip("PYROMIND_API_KEY not set")
    return api_key

@pytest.fixture(scope="session")
def base_url():
    """Get base URL from environment variable"""
    return os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")

@pytest.fixture(scope="session")
def client(api_key, base_url):
    """Create API client for tests"""
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)
```

## Test Modules

### 1. Storage Integration Tests (`test_storage_integration.py`)

**Client:** `StorageClient`

**Test Cases:**
- `test_list_files` - List files in bucket
- `test_file_exists` - Check if file exists
- `test_upload_file` - Upload a single file
- `test_download_file` - Download a file
- `test_upload_folder` - Upload folder recursively
- `test_delete_file` - Delete a single file
- `test_delete_folder` - Delete folder recursively
- `test_roundtrip` - Upload → Download → Verify content integrity

**Fixtures:**
- `storage_client` - Creates StorageClient from env vars
- `test_prefix` - UUID-based unique prefix for each test

### 2. Sandbox Integration Tests (`test_sandbox_integration.py`)

**Client:** `SandboxesClient`

**Test Cases:**
- `test_list_sandboxes` - List all sandboxes
- `test_create_sandbox` - Create a new sandbox
- `test_get_sandbox` - Get sandbox by ID
- `test_update_sandbox` - Update sandbox configuration
- `test_delete_sandbox` - Delete a sandbox
- `test_pause_resume_sandbox` - Pause and resume sandbox
- `test_execute_action` - Execute single action
- `test_execute_batch_actions` - Execute multiple actions
- `test_get_vnc` - Get VNC connection info

**Cleanup:** Delete created sandboxes in session-scoped fixture

### 3. Inference Integration Tests (`test_inference_integration.py`)

**Client:** `InferenceClient`

**Test Cases:**
- `test_list_inference_jobs` - List all jobs
- `test_create_inference_job` - Create a new job
- `test_get_inference_job` - Get job by ID
- `test_update_inference_job` - Update job configuration
- `test_delete_inference_job` - Delete a job
- `test_pause_inference_job` - Pause a running job

**Cleanup:** Delete created jobs

### 4. Training Integration Tests (`test_training_integration.py`)

**Client:** `TrainingClient`

**Test Cases:**
- `test_list_training_tasks` - List all tasks
- `test_create_training_task` - Create a new task
- `test_get_training_task` - Get task by ID
- `test_stop_training_task` - Stop a running task
- `test_delete_training_task` - Delete a task
- `test_get_node_output` - Get node output
- `test_get_node_info` - Get available node info

**Cleanup:** Delete created tasks

### 5. Instance Integration Tests (`test_instance_integration.py`)

**Client:** `InstanceClient`

**Test Cases:**
- `test_list_instances` - List all instances
- `test_create_instance` - Create a new instance
- `test_get_instance` - Get instance by ID
- `test_release_instance` - Release an instance

**Cleanup:** Release created instances

### 6. Workflow Integration Tests (`test_workflow_integration.py`)

**Module:** `workflow` (validator, converter)

**Test Cases:**
- `test_validate_workflow_valid` - Validate valid workflow
- `test_validate_workflow_invalid` - Detect invalid workflow
- `test_converter_functions` - Test workflow conversion

**Data:** Use sample workflow files from examples

## Test Execution

### Running Tests

```bash
# Run all integration tests
pytest pyromind_sdk/tests/pytest/*_integration.py -v

# Run specific module tests
pytest pyromind_sdk/tests/pytest/test_storage_integration.py -v

# Run with coverage
pytest pyromind_sdk/tests/pytest/*_integration.py --cov=pyromind_sdk.client
```

### Environment Variable Handling

- Tests gracefully skip if credentials are missing
- Each test file checks for required env vars in fixtures
- Uses `pytest.skip()` with clear error messages
- No mock fallbacks — either test runs or skips

### Resource Cleanup Strategy

- **Session-scoped cleanup fixture** runs at end of test session
- Tracks all created resources (job IDs, sandbox IDs, etc.)
- Attempts cleanup even if tests fail
- Uses `atexit` as backup for cleanup

### Test Data Management

- Create temporary test files/folders using `tempfile` module
- Use unique prefixes (UUID-based) for storage operations
- Clean up test data after each test function

## Files to Create/Update

| File | Action |
|------|--------|
| `.env` | Create with credentials (not in git) |
| `.gitignore` | Add `.env` |
| `pyromind_sdk/tests/pytest/conftest.py` | Create/renew with shared fixtures |
| `pyromind_sdk/tests/pytest/test_storage_integration.py` | Create new |
| `pyromind_sdk/tests/pytest/test_workflow_integration.py` | Create new |
| `pyromind_sdk/tests/pytest/test_sandbox_integration.py` | Update existing |
| `pyromind_sdk/tests/pytest/test_inference_integration.py` | Update existing |
| `pyromind_sdk/tests/pytest/test_training_integration.py` | Update existing |
| `pyromind_sdk/tests/pytest/test_instance_integration.py` | Update existing |
