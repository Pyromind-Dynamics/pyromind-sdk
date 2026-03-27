# Issue #3: Bare Except Clause in nodes/function_call_wrapper.py

## Severity
**CRITICAL** - Must fix before production release

## Location
- **File:** `pyromind_sdk/nodes/function_call_wrapper.py`
- **Line:** 246
- **Function:** Environment variable parsing

## Description
A bare `except:` clause is used when parsing the `PYTHON_NODE_INPUTS` environment variable. Invalid JSON is silently ignored, making configuration debugging very difficult.

## Code
```python
# Line 246
inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
try:
    inputs.update(json.loads(inputs_json))
except:
    pass  # Silently ignore invalid JSON
```

## Risk
- **Silent failures**: Invalid JSON in environment variables is ignored
- **No debugging information**: Users can't tell why their configuration isn't working
- **Catches critical exceptions**: `SystemExit`, `KeyboardInterrupt`

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/03_bare_except_function_wrapper.py
   ```
2. Observe the output confirming bare except at line 246

## Example Scenario
```bash
# User sets invalid JSON
export PYTHON_NODE_INPUTS='{invalid json}'

# Expected: Error message showing JSON parse failure
# Actual: Silent failure, inputs treated as empty dict
```

## Expected Behavior
Should catch specific JSON parsing exceptions and log them:
```python
import json
import logging

inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
try:
    inputs.update(json.loads(inputs_json))
except json.JSONDecodeError as e:
    logging.debug(f"Failed to parse PYTHON_NODE_INPUTS as JSON: {e}")
    # Continue with default inputs
```

## Impact
- **Severity:** CRITICAL
- **Affected Code:** Function call wrapper, environment variable parsing
- **User Impact:** Configuration issues are extremely difficult to debug

## Related Issues
- Issue #1: Bare except in base.py
- Issue #2: Bare except in command_executor.py

## Fix
```diff
+ import json
+ import logging

  inputs_json = os.environ.get('PYTHON_NODE_INPUTS', '{}')
  try:
      inputs.update(json.loads(inputs_json))
- except:
-     pass
+ except json.JSONDecodeError as e:
+     logging.debug(f"Failed to parse PYTHON_NODE_INPUTS as JSON: {e}")
```

## Validation
After fix, run:
```bash
python docs/validation/03_bare_except_function_wrapper.py
```
Expected: Exit code 0 (no issues found)
