# Issue #7: Missing Type Hints on Client Methods

## Severity
**HIGH** - Should fix before production release

## Locations
- `pyromind_sdk/client/sandboxes.py:212` - `request` parameter
- `pyromind_sdk/client/inference.py:88` - `request` parameter
- `pyromind_sdk/client/training.py` - various parameters
- `pyromind_sdk/client/storage.py` - various parameters

## Description
Public functions missing type hints reduce IDE autocomplete effectiveness, prevent type checking with mypy, and reduce code documentation value.

## Example
```python
# Current - Missing type hint
def update(self, sandbox_id: str, request) -> SandboxResponse:
    """Update a sandbox."""
    # What type is 'request'? Users have to read implementation.

# Expected - With type hint
def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:
    """Update a sandbox."""
    # Clear that request can be either SandboxRequest or dict
```

## Risk
- **Poor IDE support**: Autocomplete doesn't work for parameters
- **No type checking**: Can't use mypy to catch type errors
- **Hidden requirements**: Users must read source to understand types
- **Runtime errors**: Type mismatches only caught at runtime

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/07_missing_type_hints.py
   ```
2. Observe functions with missing type hints

## Expected Behavior
All public functions should have complete type hints:
```python
from typing import Union, Optional, List, Dict, Any

def update(
    self,
    sandbox_id: str,
    request: Union[SandboxRequest, dict]
) -> SandboxResponse:
    """Update a sandbox.

    Args:
        sandbox_id: The sandbox ID
        request: Update request (SandboxRequest or dict)

    Returns:
        SandboxResponse: Updated sandbox data

    Raises:
        PyroMindAPIError: If update fails
    """
    ...
```

## Impact
- **Severity:** HIGH
- **Affected Code:** All client modules
- **Developer Experience:** Reduced IDE support and type safety

## Fix Pattern
```diff
+ from typing import Union

- def update(self, sandbox_id: str, request) -> SandboxResponse:
+ def update(self, sandbox_id: str, request: Union[SandboxRequest, dict]) -> SandboxResponse:
```

## Validation
After fix, run:
```bash
python docs/validation/07_missing_type_hints.py
mypy pyromind_sdk/client/ --ignore-missing-imports
```
Expected: All public functions have type hints
