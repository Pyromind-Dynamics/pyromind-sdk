# Issue #2: Bare Except Clauses in nodes/command_executor.py

## Severity
**CRITICAL** - Must fix before production release

## Location
- **File:** `pyromind_sdk/nodes/command_executor.py`
- **Lines:** 280, 458
- **Functions:** `execute_command_template()`, command parsing logic

## Description
Multiple bare `except:` clauses in command execution logic. These catch ALL exceptions including critical ones.

## Code
```python
# Line 280 - Command parsing
except:
    parsed_args = part.split()

# Line 458 - JSON handling
except:
    pass
```

## Risk
- **Silent failures**: Errors are caught and ignored
- **No debugging information**: Can't trace command execution failures
- **Catches critical exceptions**: `SystemExit`, `KeyboardInterrupt`

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/02_bare_except_command_executor.py
   ```
2. Observe the output confirming bare except at lines 280 and 458

## Expected Behavior
Line 280 should catch parsing-specific exceptions:
```python
except (ValueError, AttributeError):
    parsed_args = part.split()
```

Line 458 should catch JSON-specific exceptions:
```python
except (ValueError, KeyError, json.JSONDecodeError):
    pass
```

## Impact
- **Severity:** CRITICAL
- **Affected Code:** Command execution, JSON parsing
- **User Impact:** Node command failures are hidden, no error messages

## Related Issues
- Issue #1: Bare except in base.py
- Issue #3: Bare except in function_call_wrapper.py

## Fix
```diff
# Line 280
- except:
+ except (ValueError, AttributeError):
      parsed_args = part.split()

# Line 458
- except:
+ except (ValueError, KeyError, json.JSONDecodeError):
      pass
```

## Validation
After fix, run:
```bash
python docs/validation/02_bare_except_command_executor.py
```
Expected: Exit code 0 (no issues found)
