"""
PyroMind Node SDK

A lightweight SDK stub for local development and testing of third-party nodes
without the full platform codebase (without `app.models.nodes`).

In the real platform runtime environment, nodes should prioritize importing
base classes from `app.models.nodes`.
"""

__version__ = "0.1.0"

# Export YAML nodes functionality
from .nodes import (
    load_nodes_from_yaml,
    load_all_nodes_from_directory,
    create_node_class_from_yaml,
    convert_node_class_to_yaml,
    yaml_to_node_class,
    python_function_to_yaml,
    PythonToYamlService,
    python_to_yaml_service,
    FunctionInfo,
    YamlConfig,
    ParameterValidation,
    validate_parameter_name,
    validate_class_name,
    validate_resource_limits,
    validate_file_path,
    validate_command_template,
    parse_input_type,
    parse_parameters,
)
# from .common.constants import (
#     MAX_YAML_FILE_SIZE,
#     MAX_PARAMETER_COUNT,
#     MAX_PARAMETER_NAME_LENGTH,
#     MAX_CLASS_NAME_LENGTH,
#     MAX_COMMAND_TEMPLATE_LENGTH,
#     ALLOWED_DTYPES,
#     MIN_MEMORY_LIMIT,
#     MAX_MEMORY_LIMIT,
#     MIN_CPU_LIMIT,
#     MAX_CPU_LIMIT,
#     MIN_GPU_COUNT,
#     MAX_GPU_COUNT,
#     PYTHON_KEYWORDS,
#     MAX_DIRECTORY_FILES,
# )

# Export API client functionality
from .client import (
    PyroMindClient,
    PyroMindAPIError,
    SandboxClient,
    InstanceClient,
    InferenceClient,
    TrainingClient,
    StorageClient,
    PyroMindAPIClient,
    EchoMindClient,
    ProfileClient,
    # Async clients
    PyroMindAsyncClient,
    PyroMindAsyncAPIError,
    AsyncSandboxClient,
    AsyncInstanceClient,
    AsyncInferenceClient,
    AsyncTrainingClient,
    AsyncEchoMindClient,
    PyroMindAsyncAPIClient,
)

# Export models
from .client.models import (
    ApiMode,
    EchoMindJobRequest,
    EchoMindJobResponse,
    ResourceConfig,
)

# Export workflow functionality
from .client.workflow import (
    WorkflowLiteConverter,
    LayoutGenerator,
    to_workflow_lite,
    to_workflow_standard,
    validate_standard_format,
)

# Export command executor utilities
from .nodes import (
    extract_placeholders,
    replace_template,
)

__all__ = [
    "__version__",
    # YAML nodes functionality
    "load_nodes_from_yaml",
    "load_all_nodes_from_directory",
    "create_node_class_from_yaml",
    "convert_node_class_to_yaml",
    "yaml_to_node_class",
    "python_function_to_yaml",
    "PythonToYamlService",
    "python_to_yaml_service",
    "FunctionInfo",
    "YamlConfig",
    "ParameterValidation",
    "validate_parameter_name",
    "validate_class_name",
    "validate_resource_limits",
    "validate_file_path",
    "validate_command_template",
    "parse_input_type",
    "parse_parameters",
    # # Constants
    # "MAX_YAML_FILE_SIZE",
    # "MAX_PARAMETER_COUNT",
    # "MAX_PARAMETER_NAME_LENGTH",
    # "MAX_CLASS_NAME_LENGTH",
    # "MAX_COMMAND_TEMPLATE_LENGTH",
    # "ALLOWED_DTYPES",
    # "MIN_MEMORY_LIMIT",
    # "MAX_MEMORY_LIMIT",
    # "MIN_CPU_LIMIT",
    # "MAX_CPU_LIMIT",
    # "MIN_GPU_COUNT",
    # "MAX_GPU_COUNT",
    # "PYTHON_KEYWORDS",
    # "MAX_DIRECTORY_FILES",
    # API client functionality
    "PyroMindClient",
    "PyroMindAPIError",
    "SandboxClient",
    "InstanceClient",
    "InferenceClient",
    "TrainingClient",
    "StorageClient",
    "PyroMindAPIClient",
    "EchoMindClient",
    "ProfileClient",
    # Async clients
    "PyroMindAsyncClient",
    "PyroMindAsyncAPIError",
    "AsyncSandboxClient",
    "AsyncInstanceClient",
    "AsyncInferenceClient",
    "AsyncTrainingClient",
    "AsyncEchoMindClient",
    "PyroMindAsyncAPIClient",
    # Models
    "ApiMode",
    "EchoMindJobRequest",
    "EchoMindJobResponse",
    "ResourceConfig",
    # Workflow functionality
    "WorkflowLiteConverter",
    "LayoutGenerator",
    "to_workflow_lite",
    "to_workflow_standard",
    "validate_standard_format",
    # Command executor
    "extract_placeholders",
    "replace_template",
]
