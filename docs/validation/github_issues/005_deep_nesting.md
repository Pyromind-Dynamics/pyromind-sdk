# Issue #5: Deep Nesting in Error Handling and Command Execution

## Severity
**HIGH** - Should fix before production release

## Locations
1. `pyromind_sdk/client/base.py:166-269` (6 levels of nesting)
2. `pyromind_sdk/nodes/command_executor.py:372-512` (7 levels of nesting)

## Description
Functions with deep nesting (4+ levels) are hard to read, test, and maintain. They violate the "Flat is better than nested" principle from PEP 20.

## Example: base.py Error Handling
```python
if not response.ok:  # Level 1
    error_data = None
    try:  # Level 2
        error_data = response.json()
    except:
        error_data = {"message": response.text}

    if isinstance(error_data, dict):  # Level 2
        if isinstance(error_data.get("message"), str):  # Level 3
            msg = error_data["message"]
            if len(msg) > 500:  # Level 4
                # ... more nesting
```

## Risk
- **Hard to understand**: Deep nesting increases cognitive load
- **Hard to test**: Nested logic is difficult to test in isolation
- **Hard to maintain**: Changes require understanding full nested context
- **Bug-prone**: Easy to miss edge cases in deeply nested code

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/05_deep_nesting.py
   ```
2. Observe functions with > 4 levels of nesting

## Expected Behavior
Extract nested logic into helper functions:
```python
def _sanitize_error_message(error_data: Dict) -> Dict:
    """Sanitize error message to avoid log flooding."""
    if isinstance(error_data, dict) and isinstance(error_data.get("message"), str):
        msg = error_data["message"]
        if len(msg) > 500:
            error_data["message"] = msg[:500] + "..."
    return error_data

# Then use in main function:
if not response.ok:
    error_data = _parse_error_response(response)
    error_data = _sanitize_error_message(error_data)
    message = _build_error_message(response.status_code, error_data, method, url)
    raise _appropriate_error(message, response.status_code)
```

## Impact
- **Severity:** HIGH
- **Affected Code:** Error handling, command execution
- **Developer Experience:** Code is harder to understand and modify

## Refactoring Plan
1. Extract `_sanitize_error_message()` helper
2. Extract `_build_error_message()` helper
3. Extract `_appropriate_error()` helper
4. Use early returns to reduce nesting

## Target
Maximum 3-4 levels of nesting per function.

## Validation
After fix, run:
```bash
python docs/validation/05_deep_nesting.py
```
Expected: No functions with > 4 levels of nesting
