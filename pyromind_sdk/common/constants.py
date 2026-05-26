"""
Common constants definition

Contains security limits and configuration constants used by YAML node
configuration loaders and other modules.
"""

# ============================================================================
# File size limits
# ============================================================================

# Maximum YAML file size (10MB)
MAX_YAML_FILE_SIZE = 10 * 1024 * 1024

# ============================================================================
# Parameter and name limits
# ============================================================================

# Maximum number of parameters
MAX_PARAMETER_COUNT = 100

# Maximum parameter name length
MAX_PARAMETER_NAME_LENGTH = 100

# Maximum class name length
MAX_CLASS_NAME_LENGTH = 100

# Maximum string default value length
MAX_STRING_DEFAULT_LENGTH = 10000

# Maximum description length
MAX_DESCRIPTION_LENGTH = 1000

# Maximum category length
MAX_CATEGORY_LENGTH = 100

# ============================================================================
# Command template limits
# ============================================================================

# Maximum total length of command template
MAX_COMMAND_TEMPLATE_LENGTH = 10000

# Maximum number of command template parts
MAX_COMMAND_TEMPLATE_PARTS = 100

# ============================================================================
# Resource limit ranges
# ============================================================================

# Memory limit range (GiB)
MIN_MEMORY_LIMIT = 1
MAX_MEMORY_LIMIT = 1000  # 1000 GiB

# CPU limit range
MIN_CPU_LIMIT = 1
MAX_CPU_LIMIT = 64

# GPU count range
MIN_GPU_COUNT = 1
MAX_GPU_COUNT = 8

# ============================================================================
# Python keywords
# ============================================================================

# Python reserved keywords (cannot be used as parameter names)
PYTHON_KEYWORDS = {
    "and", "as", "assert", "break", "class", "continue", "def", "del",
    "elif", "else", "except", "exec", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "not", "or", "pass", "print",
    "raise", "return", "try", "while", "with", "yield", "True", "False", "None"
}

# ============================================================================
# Directory loading limits
# ============================================================================

# Maximum number of files to scan in a directory
MAX_DIRECTORY_FILES = 1000

