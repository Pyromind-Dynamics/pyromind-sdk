# Issue #4: Missing Dependencies in setup.py

## Severity
**CRITICAL** - Must fix before production release

## Location
- **File:** `setup.py`
- **Section:** `install_requires`

## Description
The `setup.py` file is missing required dependencies that are used throughout the codebase. Fresh installations will fail with `ImportError`.

## Current State
```python
install_requires=[
    "pyyaml>=6.0",
],
```

## Missing Dependencies
| Package | Used In | Purpose |
|---------|---------|---------|
| `requests` | `client/base.py` | HTTP API calls |
| `pydantic` | `client/models.py` | Data validation |
| `urllib3` | `client/base.py` | HTTP connection pooling (transitive, should pin) |

## Risk
- **Installation failure**: `pip install pyromind-sdk` will appear to succeed but imports will fail
- **Broken user experience**: Users get confusing `ImportError` messages
- **Security risk**: `urllib3` is a transitive dependency with security updates - should be pinned

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/04_missing_dependencies.py
   ```
2. Observe missing dependencies
3. Test fresh install:
   ```bash
   pip uninstall pyromind-sdk -y
   pip install pyromind-sdk
   python -c "from pyromind_sdk.client import PyroMindClient"  # Fails!
   ```

## Expected Behavior
All external dependencies should be declared:
```python
install_requires=[
    "pyyaml>=6.0",
    "requests>=2.28.0",
    "pydantic>=2.0.0",
    "urllib3>=1.26.0",
],
extras_require={
    "storage": ["minio>=7.0.0"],  # Keep minio optional
},
```

## Impact
- **Severity:** CRITICAL
- **Affected Code:** All client modules
- **User Impact:** SDK is unusable after fresh installation

## Fix
Update `setup.py`:
```diff
  install_requires=[
      "pyyaml>=6.0",
+     "requests>=2.28.0",
+     "pydantic>=2.0.0",
+     "urllib3>=1.26.0",
  ],
```

## Validation
After fix, run:
```bash
python docs/validation/04_missing_dependencies.py
pip install -e .
python -c "from pyromind_sdk.client import PyroMindClient; print('Success!')"
```
Expected: No errors, imports work correctly
