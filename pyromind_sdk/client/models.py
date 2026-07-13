"""
Data models for PyroMind API Client SDK

This module defines Pydantic models for request and response data structures.
"""

from typing import Optional, List, Dict, Any, Literal, Union
from datetime import datetime, timedelta
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Common Models
class APIResponse(BaseModel):
    """Base API response model"""
    success: bool
    data: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None


class ResourceConfig(BaseModel):
    """Resource configuration model"""
    cpu: Optional[str] = None
    memory: Optional[str] = None
    gpu: Optional[str] = None  # GPU count as string (e.g., "1" for 1 GPU)
    gpu_card: Optional[str] = None  # GPU card type (e.g., "L40S", "H100")

    @field_validator('cpu', mode='before')
    @classmethod
    def validate_cpu(cls, v: Optional[Union[int, str]]) -> Optional[str]:
        """Validate and convert cpu field, accept integer or string, convert to string"""
        if v is None:
            return None
        # If integer, convert to string
        if isinstance(v, int):
            return str(v)
        # If string, return as is
        if isinstance(v, str):
            return v.strip() if v.strip() else None
        # Other types raise error
        raise ValueError(f"cpu must be an integer or string, got {type(v).__name__}")

    @field_validator('memory', mode='before')
    @classmethod
    def validate_memory(cls, v: Optional[Union[int, str]]) -> Optional[str]:
        """Validate and convert memory field, accept integer or string, convert to string with 'Gi' unit"""
        if v is None:
            return None
        # If integer, add 'Gi' unit
        if isinstance(v, int):
            return f"{v}Gi"
        # If string, return as is
        if isinstance(v, str):
            return v.strip() if v.strip() else None
        # Other types raise error
        raise ValueError(f"memory must be an integer or string, got {type(v).__name__}")

    @field_validator('gpu', mode='before')
    @classmethod
    def validate_gpu(cls, v: Optional[Union[int, str]]) -> Optional[str]:
        """Validate and convert gpu field, accept integer or string, convert to string"""
        if v is None:
            return None
        # If integer, convert to string
        if isinstance(v, int):
            return str(v)
        # If string, return as is
        if isinstance(v, str):
            return v.strip() if v.strip() else None
        # Other types raise error
        raise ValueError(f"gpu must be an integer or string, got {type(v).__name__}")


# Sandbox Models
class SandboxType(str, Enum):
    """Sandbox type enumeration"""
    # WINDOWS = "win"
    OSWORLD = "osworld"

    @classmethod
    def from_api(cls, value: str) -> 'SandboxType':
        """Convert API value to SandboxType enum"""
        # Map old API values to new enum values
        mapping = {
            # 'windows': 'win',
            'osworld': 'osworld',
        }
        normalized = mapping.get(value, value)
        return cls(normalized)


class SandboxStatus(str, Enum):
    """Sandbox status enumeration"""
    CREATING = "creating"
    STARTING = "starting"
    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class ScreenResolution(BaseModel):
    """Screen resolution model"""
    width: int
    height: int


class SandboxConfiguration(BaseModel):
    """Sandbox configuration model"""
    screen_resolution: Optional[ScreenResolution] = None
    auto_destroy: Optional[bool] = True
    vnc_password: Optional[str] = None


class SandboxRequest(BaseModel):
    """Request model for creating a sandbox"""
    sandbox_type: SandboxType
    resources: Optional[ResourceConfig] = None
    configuration: Optional[SandboxConfiguration] = None
    name: Optional[str] = None
    # OSWorld 专用：自定义系统镜像在 juicefs 上的相对路径（subPath）。
    # 仅 sandbox_type=OSWORLD 时生效；未提供时服务端使用默认镜像。
    system_image_path: Optional[str] = None


class SandboxUsage(BaseModel):
    """Sandbox usage statistics"""
    cpu_percent: Optional[float] = None
    memory_used: Optional[str] = None
    storage_used: Optional[str] = None
    # Legacy fields for backward compatibility
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    gpu_usage: Optional[float] = None


class SandboxResponse(BaseModel):
    """Sandbox response model"""
    id: str
    name: str
    type: SandboxType
    status: str
    configuration: Optional[SandboxConfiguration] = None
    usage: Optional[SandboxUsage] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    endpoint_url: Optional[str] = None
    web_vnc_url: Optional[str] = None
    uid: Optional[str] = None
    endpoint: Optional[str] = None
    screen_size: Optional[ScreenResolution] = None
    last_activity: Optional[datetime] = None
    # OSWorld 专用：自定义系统镜像在 juicefs 上的相对路径（subPath）
    system_image_path: Optional[str] = None



class SandboxListAPIResponse(BaseModel):
    """List sandboxes API response"""
    sandboxes: List[SandboxResponse]


class SandboxAPIResponse(BaseModel):
    """Single sandbox API response"""
    sandbox: SandboxResponse


# Action Models
class ActionStatus(str, Enum):
    """Action status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionParameters(BaseModel):
    """Action parameters model"""
    # Legacy fields for command execution
    command: Optional[str] = None
    working_directory: Optional[str] = None
    environment_variables: Optional[Dict[str, str]] = None
    timeout: Optional[int] = None
    # New fields for sandbox automation
    coordinates: Optional[List[int]] = None
    text: Optional[str] = None
    time: Optional[float] = None
    options: Optional[Dict[str, Any]] = None


class ActionRequest(BaseModel):
    """Action request model"""
    action: str
    parameters: Optional[ActionParameters] = None


class ActionResult(BaseModel):
    """Action result model"""
    success: bool
    message: Optional[str] = None
    screenshot: Optional[str] = None  # base64 encoded image
    coordinates: Optional[List[int]] = None
    execution_time: Optional[float] = None
    output: Optional[str] = None


class ActionResponse(BaseModel):
    """Action response model"""
    action_id: str
    action: str
    status: ActionStatus
    parameters: Optional[ActionParameters] = None
    result: Optional[ActionResult] = None
    current_state: Optional[str] = None
    timestamp: datetime

class ActionAPIResponse(BaseModel):
    """Action API response"""
    action: ActionResponse


class BatchActionRequest(BaseModel):
    """Batch action request model"""
    actions: List[ActionRequest]


class BatchActionAPIResponse(BaseModel):
    """Batch action API response"""
    actions: List[ActionResponse]


# VNC Models
class VNCConnectionInfo(BaseModel):
    """VNC connection information"""
    host: str
    port: int
    encryption: str
    auth_type: str
    # Legacy fields for backward compatibility
    password: Optional[str] = None
    websocket_url: Optional[str] = None


class VNCResponse(BaseModel):
    """VNC response model"""
    password: Optional[str] = None
    web_vnc_url: str
    connection_info: VNCConnectionInfo
    # Legacy field for backward compatibility
    connection: Optional[VNCConnectionInfo] = None


class VncAPIResponse(BaseModel):
    """VNC API response"""
    vnc: VNCResponse


# Instance (Jupyter) Models
class JupyterRequest(BaseModel):
    """Request model for creating/updating a Jupyter instance"""
    name: Optional[str] = None
    resources: Optional[ResourceConfig] = None


class JupyterResponse(BaseModel):
    """Jupyter instance response model"""
    id: str
    name: str
    status: str
    password: Optional[str] = None
    resources: Optional[ResourceConfig] = None
    url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class JupyterListAPIResponse(BaseModel):
    """List Jupyter instances API response"""
    instances: List[JupyterResponse]


class JupyterAPIResponse(BaseModel):
    """Single Jupyter instance API response"""
    instance: JupyterResponse


# Inference Models
def _normalize_startup_arg_key(key: Any) -> str:
    return str(key or "").strip()


def _normalize_startup_arg_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_startup_args(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("startup_args must be a list")

    args: List[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            text = item.strip()
            if text:
                args.append(text)
            continue
        if isinstance(item, dict):
            if any(key in item for key in ("key", "name", "arg", "value")):
                key = _normalize_startup_arg_key(item.get("key") or item.get("name") or item.get("arg"))
                arg_value = _normalize_startup_arg_value(item.get("value"))
                if not key:
                    continue
                args.append(key)
                if arg_value is not None:
                    args.append(arg_value)
                continue
            for raw_key, raw_value in item.items():
                key = _normalize_startup_arg_key(raw_key)
                arg_value = _normalize_startup_arg_value(raw_value)
                if not key:
                    continue
                args.append(key)
                if arg_value is not None:
                    args.append(arg_value)
            continue
        raise ValueError("startup_args items must be strings or dictionaries")
    return args


class InferenceJobRequest(BaseModel):
    """
    Request model for creating an inference job
        @inf_image 从get_inf_image 获取
        @inference_framework 从get_framework获取
    """
    model_path: str
    inference_framework: Optional[str] = None
    resources: Optional[ResourceConfig] = None
    name: Optional[str] = None
    inf_image: Optional[str] = None
    model_name: Optional[str] = None
    model_length: Optional[int] = None
    startup_args: Optional[List[Union[str, Dict[str, Any]]]] = None

    @field_validator("startup_args", mode="before")
    @classmethod
    def validate_startup_args(cls, value: Any) -> Optional[List[str]]:
        return _normalize_startup_args(value)




class InferenceJobResponse(BaseModel):
    """Inference job response model"""
    id: str = Field(alias="job_id")
    name: str
    model_path: str
    inference_framework: Optional[str] = None
    image: Optional[str] = None
    status: str
    uid: Optional[str] = None
    resources: Optional[ResourceConfig] = None
    endpoint_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_name: Optional[str] = None
    model_length: Optional[int] = None
    inf_image: Optional[str] = None
    startup_args: Optional[List[str]] = None


class InferenceJobListAPIResponse(BaseModel):
    """List inference jobs API response"""
    inference_jobs: List[InferenceJobResponse] = Field(default_factory=list)
    page: Optional[int] = None
    page_size: Optional[int] = None
    total: Optional[int] = None
    has_more: Optional[bool] = None


class InferenceJobAPIResponse(BaseModel):
    """Single inference job API response"""
    job: InferenceJobResponse


class InferenceJobCreateAPIResponse(BaseModel):
    """Create inference job API response"""
    job_id: str


# Training Models
class TrainingFramework(str, Enum):
    """Training framework enumeration"""
    verl = "verl"
    slime = "slime"


class TrainingTaskCreateRequest(BaseModel):
    """Request model for creating a training task"""
    name: str
    workflow: Dict[str, Any]  # Workflow JSON structure similar to convert_workflow_to_prompt's workflow field
    out_id: str  # Map external system task ID


class WorkflowRunRequest(BaseModel):
    """Request model for running a workflow with injected primitive node values"""
    workflow_name: str
    primitive_node_map: Dict[str, Any] = {}
    execution_argos: Optional[list]


class CreateTrainingNodeRequest(BaseModel):
    """Request model for creating a custom training node"""
    yaml_path: Optional[str] = Field(None, description="Path to YAML file (mutually exclusive with yaml_content)")
    yaml_content: Optional[str] = Field(None, description="YAML config content (mutually exclusive with yaml_path)")
    source_file_path: Optional[str] = Field(None, description="Source Python file path (required for direct mode)")
    function_name: Optional[str] = Field(None, description="Function name in source file (required for direct mode)")
    category: str = Field(default="", description="Node category")
    cover: bool = Field(default=False, description="Overwrite existing node if name conflicts")


class MoveNodeRequest(BaseModel):
    """Request model for moving a node to a new source file path"""
    node_name: str = Field(..., description="Node name to move")
    source_file_path: str = Field(..., description="New source file path")


class TrainingTaskNodeInfo(BaseModel):
    """Training task node information"""
    node_id: int
    task_id: int
    node_name: str
    start_at: Optional[Union[str, datetime]] = None
    end_at: Optional[Union[str, datetime]] = None
    duration: Optional[Union[str, timedelta]] = None  # Duration as string or timedelta object
    resources: Optional[ResourceConfig] = None  # Resource configuration (CPU, memory, GPU)
    amount: Optional[float] = None  # Cost amount in float
    url: Optional[str] = None
    wand_flag: Optional[str] = None
    
    @model_validator(mode='before')
    @classmethod
    def convert_resources(cls, data: Any) -> Any:
        """Convert cpu_num, cpu_memory, gpu_num fields to ResourceConfig object
        
        This validator converts the legacy resource fields (cpu_num, cpu_memory, gpu_num)
        from the API response to a ResourceConfig object in the resources field.
        """
        if not isinstance(data, dict):
            return data
        
        cpu_num = data.get("cpu_num")
        cpu_memory = data.get("cpu_memory")
        gpu_num = data.get("gpu_num")
        
        # If resources field already exists, use it
        if "resources" in data and data["resources"] is not None:
            return data
        
        # Parse GPU information: format may be "H100*4" or "4"
        gpu_count = None
        gpu_card = None
        if gpu_num and gpu_num != "-":
            gpu_str = str(gpu_num).strip()
            if "*" in gpu_str:
                parts = gpu_str.split("*")
                if len(parts) == 2:
                    gpu_card = parts[0].strip()
                    try:
                        gpu_count = int(parts[1].strip())
                    except (ValueError, IndexError):
                        gpu_count = None
            else:
                try:
                    gpu_count = int(gpu_str)
                except (ValueError, TypeError):
                    gpu_count = None
        
        # Process CPU: convert from "4c" format to string
        cpu_str = None
        if cpu_num and cpu_num != "-":
            cpu_str = str(cpu_num).replace("c", "") if "c" in str(cpu_num) else str(cpu_num)
        
        # Process memory: already in "80Gi" format
        memory_str = None
        if cpu_memory and cpu_memory != "-":
            memory_str = str(cpu_memory)
        
        # Create ResourceConfig - always create it, even if all fields are None
        # This ensures consistent behavior: resources will be an empty ResourceConfig
        # if no resource information is available, rather than None
        resources_dict = {}
        if cpu_str:
            resources_dict["cpu"] = cpu_str
        if memory_str:
            resources_dict["memory"] = memory_str
        if gpu_count is not None:
            resources_dict["gpu"] = str(gpu_count)
        if gpu_card:
            resources_dict["gpu_card"] = gpu_card
        
        # Only set resources if we have at least one valid field
        # If all fields are None or "-", set resources to None
        if cpu_str or memory_str or gpu_count is not None:
            data["resources"] = resources_dict
        # else: resources remains None (default value)
        
        return data
    
    @field_validator('amount', mode='before')
    @classmethod
    def parse_amount(cls, v):
        """Parse amount from string or number to float"""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            v = v.strip()
            if v == "" or v == "-":
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None
        return None
    
    @field_validator('duration', mode='before')
    @classmethod
    def parse_duration(cls, v):
        """Parse duration from string to timedelta
        
        Handles formats like:
        - "00:22:16" -> timedelta(hours=0, minutes=22, seconds=16)
        - "1day 02:30:45" -> timedelta(days=1, hours=2, minutes=30, seconds=45)
        - "2days 05:10:20" -> timedelta(days=2, hours=5, minutes=10, seconds=20)
        """
        if v is None:
            return None
        if isinstance(v, timedelta):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v == "" or v == "-":
                return None
            try:
                # Parse format like "1day 02:30:45" or "2days 05:10:20"
                if "day" in v.lower():
                    # Format: "Nd HH:MM:SS" or "Ndays HH:MM:SS"
                    parts = v.split()
                    days = 0
                    time_str = ""
                    
                    for part in parts:
                        part_lower = part.lower()
                        if "day" in part_lower:
                            # Extract number from "1day" or "2days"
                            import re
                            match = re.search(r'(\d+)', part)
                            if match:
                                days = int(match.group(1))
                        elif ":" in part:
                            time_str = part
                    
                    if time_str:
                        # Parse HH:MM:SS
                        time_parts = time_str.split(":")
                        if len(time_parts) == 3:
                            hours = int(time_parts[0])
                            minutes = int(time_parts[1])
                            seconds = int(time_parts[2])
                            return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
                    elif days > 0:
                        return timedelta(days=days)
                
                # Parse format like "HH:MM:SS" or "MM:SS"
                elif ":" in v:
                    time_parts = v.split(":")
                    if len(time_parts) == 3:
                        # HH:MM:SS
                        hours = int(time_parts[0])
                        minutes = int(time_parts[1])
                        seconds = int(time_parts[2])
                        return timedelta(hours=hours, minutes=minutes, seconds=seconds)
                    elif len(time_parts) == 2:
                        # MM:SS
                        minutes = int(time_parts[0])
                        seconds = int(time_parts[1])
                        return timedelta(minutes=minutes, seconds=seconds)
                
                # Try to parse as seconds (integer string)
                try:
                    seconds = float(v)
                    return timedelta(seconds=seconds)
                except ValueError:
                    return None
            except (ValueError, TypeError, IndexError):
                return None
        return None
    
    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime from string if needed"""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                # Try ISO format first (most common)
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                try:
                    # Try common datetime formats
                    from datetime import datetime as dt
                    # Try various formats
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                        try:
                            return dt.strptime(v, fmt)
                        except ValueError:
                            continue
                    return None
                except (ValueError, TypeError):
                    return None
        return None
    


class TrainingTaskCreateResponse(BaseModel):
    """Training task create response model"""
    task_id: str
    name: str
    status: str  # Using task status from backend
    metrics: Optional[Dict[str, Any]] = None  # Training metrics
    nodes: Optional[List[TrainingTaskNodeInfo]] = None  # List of nodes in this task
    created_at: Optional[Union[str, datetime]] = None
    started_at: Optional[Union[str, datetime]] = None


class TrainingTaskResponse(BaseModel):
    """Training task response model"""
    task_id: str
    name: str
    status: str  # Using task status from backend
    metrics: Optional[Dict[str, Any]] = None  # Training metrics
    nodes: Optional[List[TrainingTaskNodeInfo]] = None  # List of nodes in this task
    created_at: Optional[Union[str, datetime]] = None
    started_at: Optional[Union[str, datetime]] = None
    completed_at: Optional[Union[str, datetime]] = None
    expires_at: Optional[Union[str, datetime]] = None
    error_message: Optional[str] = None
    
    @field_validator('created_at', 'started_at', 'completed_at', 'expires_at', mode='before')
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime from string if needed"""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                # Try ISO format first (most common)
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                try:
                    # Try common datetime formats
                    from datetime import datetime as dt
                    # Try various formats
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                        try:
                            return dt.strptime(v, fmt)
                        except ValueError:
                            continue
                    return None
                except (ValueError, TypeError):
                    return None
        return None


class TrainingTaskListAPIResponse(BaseModel):
    """List training jobs API response"""
    tasks: List[TrainingTaskResponse]


class TrainingTaskAPIResponse(BaseModel):
    """Single training job API response"""
    job: TrainingTaskResponse


# EchoMind Models
class ApiMode(str, Enum):
    """API mode enumeration for EchoMind"""
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"


class ExecutionMode(str, Enum):
    """Execution mode enumeration for EchoMind"""
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class EchoMindJobRequest(BaseModel):
    """Request model for creating/updating an EchoMind instance"""
    name: str
    api_url: str
    api_mode: str = Field(
        default="openai",
        description=f"API mode, must be one of: {', '.join(m.value for m in ApiMode)}"
    )
    origin_model: str
    access_key: str
    training_model: str
    training_batch_size: int
    trajectory_buffer_size: int
    time_per_round: float
    training_round: int
    training_save_path: str
    resources: Optional[ResourceConfig] = None
    execution_mode: str = Field(
        default="manual",
        description=f"Execution mode, must be one of: {', '.join(m.value for m in ExecutionMode)}"
    )
    custom_runtime_script_path: Optional[str] = None   ## 自定义脚本地址

    @field_validator('api_mode', mode='before')
    @classmethod
    def validate_api_mode(cls, v: str) -> str:
        """Validate and normalize api_mode field"""
        if v is None:
            raise ValueError("api_mode is required")
        
        # Normalize to lowercase
        v_lower = v.lower().strip() if isinstance(v, str) else str(v).lower().strip()
        
        # Map common variations to valid values
        mode_mapping = {
            'openai': 'openai',
            'gemini': 'gemini',
            'anthropic': 'anthropic',
        }
        
        normalized = mode_mapping.get(v_lower, v_lower)
        
        valid_modes = ['openai', 'gemini', 'anthropic']
        if normalized not in valid_modes:
            raise ValueError(f"api_mode must be one of {valid_modes}, got: {v}")
        
        return normalized


class EchoMindJobResponse(BaseModel):
    """EchoMind instance response model"""
    job_id: str
    name: Optional[str] = None
    status: str
    api_url: Optional[str] = None
    api_mode: Optional[str] = None
    origin_model: Optional[str] = None
    access_key: Optional[str] = None
    training_model: Optional[str] = None
    training_batch_size: Optional[str] = None
    trajectory_buffer_size: Optional[str] = None
    time_per_round: Optional[str] = None
    training_round: Optional[str] = None
    training_save_path: Optional[str] = None
    secret_key: Optional[str] = None
    resources: Optional[ResourceConfig] = None
    created_at: Optional[Union[str, datetime]] = None
    execution_mode: str = None
    custom_runtime_script_path: Optional[str] = None   ## 自定义脚本地址


class EchoMindJobListAPIResponse(BaseModel):
    """List EchoMind instances API response"""
    echomind_jobs: List[EchoMindJobResponse] = Field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None


class EchoMindJobAPIResponse(BaseModel):
    """Single EchoMind instance API response"""
    job: EchoMindJobResponse


class EchoMindJobCreateAPIResponse(BaseModel):
    """Create EchoMind instance API response"""
    job_id: str


class AvatarInfo(BaseModel):
    """Avatar information model"""
    filename: Optional[str] = None
    object_path: Optional[str] = None
    size_mb: Optional[float] = None
    etag: Optional[str] = None
    version_id: Optional[str] = None
    url: Optional[str] = None


class ProfileUserInfo(BaseModel):
    """Profile user information model"""
    avatar_info: Optional[AvatarInfo] = None
    avatar_url: Optional[str] = None
    email: Optional[str] = None
    full_phone_num: Optional[str] = None
    username: Optional[str] = None
    uid: Optional[Union[str, int]] = None
    group_id: Optional[int] = None
    user_tier: Optional[str] = None
    credit_amount: Optional[Union[int, float, str]] = None
    cash_balance: Optional[str] = None
    available_credit: Optional[str] = None
    currency: Optional[str] = None
    account_restricted: Optional[bool] = None


class ProfileUserInfoResponse(BaseModel):
    """Profile user info response model"""
    model_config = ConfigDict(populate_by_name=True)

    is_logged_in: bool = Field(alias="isLoggedIn")
    user: ProfileUserInfo


class ProfileAccessKeyResponse(BaseModel):
    """Profile access key response model"""
    access_key: str = Field(alias="accessKey")


class ProfileStorageInfoResponse(BaseModel):
    """Profile storage info response model"""
    access_key: str
    secret_key: Optional[str] = None
    url: Optional[str] = None
    uid: Optional[str] = None


class UserPubKeyRequest(BaseModel):
    """User public key request model"""
    name: Optional[str] = None
    key: Optional[str] = None
    id: Optional[int] = None


class UserPubKey(BaseModel):
    """User public key model"""
    id: Optional[int] = None
    user_id: Optional[int] = None
    pub_key: Optional[str] = None
    name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    feature: Optional[Union[str, Dict[str, Any]]] = None
    is_deleted: Optional[int] = None
    version: Optional[int] = None
    key_type: Optional[str] = None


class UserPubKeyListResponse(BaseModel):
    """User public key list response model"""
    keys: List[UserPubKey] = Field(default_factory=list)





class TrainingTaskEventType(str, Enum):
    """Training task event type enumeration"""
    TRAINING_TASK_STATUS_CHANGED = "training_task_status_changed"
    TRAINING_NODE_STATUS_CHANGED = "training_node_status_changed"



