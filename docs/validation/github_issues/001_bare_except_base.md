# Issue #1: Bare Except Clause in client/base.py

## Severity
**CRITICAL** - Must fix before production release

## Location
- **File:** `pyromind_sdk/client/base.py`
- **Line:** 170
- **Function:** `_request()`

## Description
A bare `except:` clause is used when parsing JSON error responses. This catches ALL exceptions including `SystemExit`, `KeyboardInterrupt`, and `GeneratorExit`, which should never be caught.

## Code
```python
# Line 170
except:
    error_data = {"message": response.text}
```

## Risk
- **Hides critical errors**: Unexpected exceptions are silently caught
- **Prevents application termination**: `SystemExit` and `KeyboardInterrupt` are caught
- **Makes debugging impossible**: No way to trace unexpected failures

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/01_bare_except_base.py
   ```
2. Observe the output confirming bare except at line 170

## Expected Behavior
Should catch specific exception types:
```python
except (json.JSONDecodeError, ValueError, AttributeError):
    error_data = {"message": response.text}
```

## Impact
- **Severity:** CRITICAL
- **Affected Code:** Error handling for all API requests
- **User Impact:** Application may become unresponsive to termination signals

## Related Issues
- Issue #2: Bare except in command_executor.py
- Issue #3: Bare except in function_call_wrapper.py

## Fix
```diff
- except:
+ except (json.JSONDecodeError, ValueError, AttributeError):
      error_data = {"message": response.text}
```

Also ensure `json` is imported at the top of the file.

## Validation
After fix, run:
```bash
python docs/validation/01_bare_except_base.py
```
Expected: Exit code 0 (no issues found)
