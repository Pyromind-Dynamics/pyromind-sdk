# Issue #8: Magic Numbers Without Named Constants

## Severity
**MEDIUM** - Should fix for better code maintainability

## Locations
- `pyromind_sdk/client/base.py` - `500` (max error message length)
- `pyromind_sdk/nodes/command_executor.py` - `0.1` (sleep delay), `5` (max preview lines)

## Description
Magic numbers are hardcoded numeric values without clear meaning. They make code harder to understand and maintain.

## Examples
```python
# base.py:175 - Why 500?
if len(msg) > 500:
    error_data["message"] = msg[:500] + "..."

# command_executor.py:531 - Why 0.1?
time.sleep(0.1)

# command_executor.py:198 - Why 5?
if len(lines) > 5:
    # ...
```

## Risk
- **Hard to understand**: What does `500` represent?
- **Hard to modify**: If value needs to change, must find all occurrences
- **Inconsistent values**: Different parts of code might use different values

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/08_magic_numbers.py
   ```
2. Observe magic numbers in the code

## Expected Behavior
Define constants in `pyromind_sdk/common/constants.py`:

```python
# Error handling limits
MAX_ERROR_MESSAGE_LENGTH = 500

# Command execution timing
OUTPUT_FILE_READ_DELAY = 0.1  # seconds

# Display limits
MAX_PREVIEW_LINES = 5
```

Then use them in code:
```python
# base.py
from pyromind_sdk.common.constants import MAX_ERROR_MESSAGE_LENGTH

if len(msg) > MAX_ERROR_MESSAGE_LENGTH:
    error_data["message"] = msg[:MAX_ERROR_MESSAGE_LENGTH] + "..."

# command_executor.py
from pyromind_sdk.common.constants import OUTPUT_FILE_READ_DELAY, MAX_PREVIEW_LINES

time.sleep(OUTPUT_FILE_READ_DELAY)

if len(lines) > MAX_PREVIEW_LINES:
    # ...
```

## Impact
- **Severity:** MEDIUM
- **Affected Code:** Error handling, command execution
- **Maintainability**: Code is easier to understand and modify

## Benefits
- **Self-documenting**: `MAX_ERROR_MESSAGE_LENGTH` is clearer than `500`
- **Single source of truth**: Change in one place affects all uses
- **Consistency**: All code uses the same values

## Validation
After fix, run:
```bash
python docs/validation/08_magic_numbers.py
```
Expected: No magic numbers found
