# Issue #6: Functions Exceeding 50 Lines

## Severity
**HIGH** - Should fix before production release

## Locations
1. `pyromind_sdk/client/base.py:_request()` - 151 lines (129-280)
2. `pyromind_sdk/nodes/command_executor.py:execute_command_template()` - 257 lines (313-570)
3. `pyromind_sdk/nodes/yaml_loader.py:create_node_class_from_yaml()` - 226 lines (451-677)

## Description
Functions > 50 lines violate Single Responsibility Principle. They are difficult to test, debug, and understand. These functions are doing too many things.

## Why This Matters
- **Testing**: Can't unit test individual parts easily
- **Understanding**: Hard to grasp what the function does
- **Maintaining**: Changes in one area can affect others
- **Reusing**: Large functions have limited reusability

## Reproduction Steps
1. Run the validation script:
   ```bash
   python docs/validation/06_long_functions.py
   ```
2. Observe functions > 50 lines

## Expected Behavior
Functions should be < 50 lines. Break down into smaller, focused helpers.

## Example Refactoring: execute_command_template()

**Current:** 257 lines doing:
- Command parsing
- Placeholder substitution
- Output file creation
- Command execution
- Output file reading
- Error handling

**Refactored:**
```python
def execute_command_template(...) -> Dict[str, Any]:
    """Execute command template with input substitution."""
    inputs = inputs or {}
    command_parts, output_files = _prepare_command(command_template, inputs, output_names)
    actual_command = _substitute_placeholders(command_parts, inputs, output_files)
    result = _execute_shell_command(actual_command, timeout)
    outputs = _read_output_files(output_files)
    return {**result, "outputs": outputs}

def _prepare_command(...):
    """Prepare command by substituting inputs and creating output files."""
    # ~30 lines

def _substitute_placeholders(...):
    """Substitute {{placeholder}} values in command."""
    # ~40 lines

def _execute_shell_command(...):
    """Execute shell command with timeout."""
    # ~30 lines

def _read_output_files(...):
    """Read output from temporary files."""
    # ~20 lines
```

## Impact
- **Severity:** HIGH
- **Affected Code:** Core SDK functionality
- **Developer Experience:** Reduced code maintainability

## Refactoring Plan
1. Extract `_prepare_command()` from `execute_command_template()`
2. Extract `_substitute_placeholders()` from `execute_command_template()`
3. Extract `_execute_shell_command()` from `execute_command_template()`
4. Extract `_read_output_files()` from `execute_command_template()`
5. Extract error helpers from `_request()`

## Target
- Maximum 50 lines per function
- Ideal: 10-30 lines per function
- Each function does ONE thing

## Validation
After fix, run:
```bash
python docs/validation/06_long_functions.py
```
Expected: No functions > 50 lines
