"""
Storage API Client

This module provides a client for managing file storage operations via MinIO/S3-compatible storage.
"""

import os
import mimetypes
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, BinaryIO, Union
from collections import deque
from urllib.parse import urlparse

try:
    from minio import Minio
    from minio.error import S3Error
    from minio.deleteobjects import DeleteObject
except ImportError:
    raise ImportError(
        "minio package is required for storage operations. "
        "Please install it with: pip install minio"
    )

from pyromind_sdk.client.base import PyroMindAPIError, DEFAULT_API_BASE_URL, DEFAULT_CLUSTER, ENV_API_KEY, ENV_BASE_URL, ENV_CLUSTER


# Constants
DEFAULT_STORAGE_ENDPOINT = "https://storage.pyromind.ai"
DEFAULT_REGION = "us-east-1"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
ENV_STORAGE_ENDPOINT = "PYROMIND_STORAGE_ENDPOINT"
ENV_STORAGE_SECRET_KEY = "PYROMIND_STORAGE_SECRET_KEY"
ENV_STORAGE_BUCKET = "PYROMIND_STORAGE_BUCKET"


class StorageClient:
    """
    Client for managing file storage operations
    
    Provides methods for listing files, checking file existence,
    uploading files/folders, and downloading files.
    
    The client uses MinIO/S3-compatible storage and requires:
    - Storage endpoint URL
    - Access key (from PYROMIND_API_KEY env var)
    - Secret key (from PYROMIND_STORAGE_SECRET_KEY env var)
    - Bucket name (from PYROMIND_STORAGE_BUCKET env var, optional)
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        secure: bool = False,
        region: Optional[str] = None
    ):
        """
        Initialize the Storage Client

        Args:
            endpoint: Storage endpoint URL (e.g., "https://storage.pyromind.ai")
                    If not provided, reads from PYROMIND_STORAGE_ENDPOINT env var
                    Defaults to "https://storage.pyromind.ai" if not set
            access_key: Access key for storage authentication
                       If not provided, reads from PYROMIND_API_KEY env var
            secret_key: Secret key for storage authentication
                       If not provided, reads from PYROMIND_STORAGE_SECRET_KEY env var
            bucket_name: Default bucket name to use
                        If not provided, reads from PYROMIND_STORAGE_BUCKET env var
            secure: Whether to use HTTPS (default: False)
            region: Storage region (optional)

        Raises:
            ValueError: If required credentials are not provided
        """
        # Get endpoint from parameter or environment variable
        if endpoint is None:
            endpoint = os.getenv(ENV_STORAGE_ENDPOINT, DEFAULT_STORAGE_ENDPOINT)

        if not endpoint:
            raise ValueError(
                f"Storage endpoint is required. Please provide it either as a parameter "
                f"or set the {ENV_STORAGE_ENDPOINT} environment variable."
            )

        # Get access key from parameter or environment variable
        if access_key is None:
            access_key = os.getenv(ENV_API_KEY)

        if not access_key:
            raise ValueError(
                f"Storage access key is required. Please provide it either as a parameter "
                f"or set the {ENV_API_KEY} environment variable."
            )

        # Get secret key from parameter or environment variable
        if secret_key is None:
            secret_key = os.getenv(ENV_STORAGE_SECRET_KEY)

        if not secret_key:
            raise ValueError(
                f"Storage secret key is required. Please provide it either as a parameter "
                f"or set the {ENV_STORAGE_SECRET_KEY} environment variable."
            )

        # Get bucket name from parameter or environment variable
        if bucket_name is None:
            bucket_name = os.getenv(ENV_STORAGE_BUCKET)
        
        # Strip whitespace
        endpoint = endpoint.strip()
        access_key = access_key.strip()
        secret_key = secret_key.strip()

        # Parse endpoint URL to extract hostname and port
        # MinIO client expects format like "hostname:port" or "hostname"
        # If endpoint contains https:// or http://, extract the hostname and set secure flag

        # Check if endpoint is a URL with scheme
        if endpoint.startswith(('http://', 'https://')):
            parsed = urlparse(endpoint)
            
            # Set secure flag based on scheme
            if parsed.scheme == "https":
                secure = True
            elif parsed.scheme == "http":
                secure = False
            
            # Extract hostname and port from netloc (which is hostname:port)
            # netloc format: hostname:port or just hostname
            netloc = parsed.netloc
            if ':' in netloc:
                # Has port
                endpoint = netloc
            else:
                # No port, use just hostname
                endpoint = netloc
        # If no scheme, assume it's already in the correct format (hostname:port or hostname)
        
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.default_bucket = bucket_name
        self.secure = secure
        self.region = region
        
        # Initialize MinIO client
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region
        )
        
        # Pre-populate region cache for default bucket to avoid auto-detection
        # which may require s3:GetBucketLocation permission that some S3-compatible
        # services don't grant. This allows operations to proceed without region lookup.
        # Use DEFAULT_REGION as default region for S3-compatible services
        self._current_bucket = None
        if bucket_name and hasattr(self.client, '_region_map'):
            # Set region to DEFAULT_REGION to avoid GetBucketLocation API call
            # Most S3-compatible services work with this default region
            self.client._region_map[bucket_name] = DEFAULT_REGION

    @property
    def current_bucket(self) -> Optional[str]:
        """
        Get the current default bucket name.

        Returns the validated default bucket name, or None if no default is set.
        This property is useful when you want to use the default bucket without
        passing bucket_name to every method.

        Returns:
            Default bucket name or None
        """
        if self._current_bucket is None and self.default_bucket:
            self._current_bucket = self.default_bucket
            self._cache_region(self._current_bucket)
        return self._current_bucket
    
    def _ensure_bucket(self, bucket_name: str) -> None:
        """
        Ensure bucket exists, create if it doesn't.
        
        Note: Some S3-compatible storage services may not support bucket_exists()
        due to permission differences. In such cases, we skip the check and let
        the actual operation (list_objects, etc.) fail if the bucket doesn't exist.
        """
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
        except S3Error as e:
            # If bucket_exists() fails due to AccessDenied or other permission issues,
            # we skip the check and let the actual operation handle the error.
            # This is common with some S3-compatible storage services where
            # list_buckets() works but bucket_exists() requires additional permissions.
            # s5cmd and other tools work because they use different API calls.
            if e.code == "AccessDenied":
                # Skip bucket existence check, let the actual operation fail if needed
                pass
            else:
                # Re-raise other errors
                raise
    
    def _get_bucket(self, bucket_name: Optional[str] = None) -> str:
        """
        Get bucket name, using default if not provided.

        Args:
            bucket_name: Bucket name override, or None to use default

        Returns:
            Validated bucket name

        Raises:
            ValueError: If no bucket name is available
        """
        if bucket_name is None:
            bucket = self.current_bucket
            if bucket is None:
                raise ValueError(
                    f"Bucket name is required. Please provide it either as a parameter "
                    f"or set the {ENV_STORAGE_BUCKET} environment variable."
                )
            return bucket

        # Custom bucket name provided - cache its region and return
        self._cache_region(bucket_name)
        return bucket_name

    def _cache_region(self, bucket: str) -> None:
        """
        Cache region for a bucket to avoid GetBucketLocation API call.

        This is needed because some S3-compatible storage services don't grant
        s3:GetBucketLocation permission, which can cause operations to fail.
        """
        if hasattr(self.client, '_region_map') and bucket not in self.client._region_map:
            self.client._region_map[bucket] = DEFAULT_REGION
    
    def list_files(
        self,
        folder_path: str = "",
        bucket_name: Optional[str] = None,
        recursive: bool = True,
        max_depth: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        List files and folders in a directory
        
        Args:
            folder_path: Path to the folder (e.g., "documents/" or "images/2024/")
            bucket_name: Bucket name (uses default if not provided)
            recursive: Whether to list files recursively (default: True)
            max_depth: Maximum depth for recursive listing (None for unlimited, default: None)
                      Only applies when recursive=True
        
        Returns:
            List of file/folder information dictionaries, each containing:
            - object_name: Full path to the file or folder
            - size: File size in bytes (0 for folders)
            - last_modified: Last modification time
            - etag: ETag of the object
            - type: "file" or "folder"
        """
        bucket = self._get_bucket(bucket_name)
        self._ensure_bucket(bucket)
        
        # Normalize folder_path to prefix
        if folder_path:
            prefix = folder_path.rstrip("/") + "/"
        else:
            prefix = ""
        
        # If recursive=True and max_depth is specified, use optimized layer-by-layer traversal
        # instead of reading all objects and filtering
        if recursive and max_depth is not None:
            return self._list_files_with_max_depth(bucket, prefix, max_depth)
        
        # Otherwise, use standard recursive or non-recursive listing
        items = []
        try:
            objects = self.client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=recursive
            )
            
            for obj in objects:
                # Determine if it's a folder (ends with /) or file
                is_folder = obj.object_name.endswith("/")
                
                items.append({
                    "object_name": obj.object_name,
                    "size": 0 if is_folder else obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                    "etag": obj.etag,
                    "type": "folder" if is_folder else "file"
                })
        except S3Error as e:
            raise ValueError(f"Failed to list files: {e.message}")
        
        return items
    
    def _list_files_with_max_depth(
        self,
        bucket: str,
        prefix: str,
        max_depth: int
    ) -> List[Dict[str, Any]]:
        """
        List files and folders with max_depth limit using layer-by-layer traversal.
        This is more efficient than reading all objects and filtering.
        
        Args:
            bucket: Bucket name
            prefix: Path prefix to start from
            max_depth: Maximum depth to traverse (0 = only direct children, 1 = direct children + first level, etc.)
        
        Returns:
            List of file/folder information dictionaries
        """
        items = []
        # Queue stores (folder_path, folder_depth) tuples
        # folder_depth is the depth of the folder itself (0 for starting prefix)
        queue = deque([(prefix, 0)])
        seen_paths = set()  # Track seen paths to avoid duplicates
        
        try:
            while queue:
                current_prefix, folder_depth = queue.popleft()
                
                # Note: We don't skip if current_prefix is in seen_paths because
                # the folder itself might have been seen as an object, but we still
                # need to list its contents. We only track individual objects to avoid duplicates.
                
                # Calculate the depth of items in this folder
                # Items in the starting folder have depth 1, items in subfolders have depth = folder_depth + 1
                item_depth = folder_depth + 1
                
                # If item_depth exceeds max_depth + 1, skip listing this folder
                # max_depth=0 means only direct children (depth 1), max_depth=1 means direct children + first level (depth 2), etc.
                if item_depth > max_depth + 1:
                    continue
                
                # List objects in current directory (non-recursive)
                objects = self.client.list_objects(
                    bucket_name=bucket,
                    prefix=current_prefix,
                    recursive=False
                )
                
                for obj in objects:
                    # Skip if already seen
                    if obj.object_name in seen_paths:
                        continue
                    seen_paths.add(obj.object_name)
                    
                    # Determine if it's a folder (ends with /) or file
                    is_folder = obj.object_name.endswith("/")
                    
                    items.append({
                        "object_name": obj.object_name,
                        "size": 0 if is_folder else obj.size,
                        "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                        "etag": obj.etag,
                        "type": "folder" if is_folder else "file"
                    })
                    
                    # If it's a folder and we haven't reached max_depth, add to queue
                    # The folder's depth is item_depth, which will be used when listing its contents
                    # We add to queue if item_depth <= max_depth (so max_depth=1 will include first level subdirectories)
                    if is_folder and item_depth <= max_depth:
                        queue.append((obj.object_name, item_depth))
                        
        except S3Error as e:
            raise ValueError(f"Failed to list files: {e.message}")
        
        return items
    
    def file_exists(
        self,
        file_path: str,
        bucket_name: Optional[str] = None
    ) -> bool:
        """
        Check if a file exists
        
        Args:
            file_path: Path to the file (e.g., "documents/file.txt")
            bucket_name: Bucket name (uses default if not provided)
        
        Returns:
            True if file exists, False otherwise
        """
        bucket = self._get_bucket(bucket_name)
        
        try:
            self.client.stat_object(bucket, file_path)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise ValueError(f"Failed to check file existence: {e.message}")

    def upload_file(
            self,
            file_path: Union[str, Path, BinaryIO],
            object_name: str,
            bucket_name: Optional[str] = None,
            content_type: Optional[str] = None,
            part_size: int = 0,  # 0 表示由 MinIO 自动计算（最小 5MB，最大 5GB）
            num_parallel_uploads: int = 3  # 分块并发上传的线程数，大文件建议调大（如 5 或 10）
    ) -> Dict[str, Any]:
        """
        Upload a file to storage (Automatically handles multipart upload for large files)

        Args:
            file_path: Local file path or file-like object to upload
            object_name: Destination object name in storage (e.g., "documents/file.txt")
            bucket_name: Bucket name (uses default if not provided)
            content_type: MIME type of the file (auto-detected if not provided)
            part_size: Size of each multipart chunk in bytes (0 for auto-calculation， Minimum 5MB, maximum 5GB)
            num_parallel_uploads: Number of concurrent parallel uploads for chunks（default：3，big file override 5 -10）

        Returns:
            Dictionary containing upload result:
            - object_name: Uploaded object name
            - etag: ETag of the uploaded object
            - size: Size of the uploaded file
        """
        bucket = self._get_bucket(bucket_name)
        self._ensure_bucket(bucket)

        # Handle file path or file-like object
        original_path = None
        if isinstance(file_path, (str, Path)):
            original_path = Path(file_path)
            if not original_path.exists():
                raise FileNotFoundError(f"File not found: {original_path}")

            file_size = original_path.stat().st_size
            file_obj = open(original_path, "rb")
            should_close = True
        else:
            # Assume it's a file-like object
            file_obj = file_path
            file_obj.seek(0, 2)  # Seek to end
            file_size = file_obj.tell()
            file_obj.seek(0)  # Seek back to start
            should_close = False

        try:
            # Auto-detect content type if not provided
            if content_type is None and original_path is not None:
                content_type, _ = mimetypes.guess_type(str(original_path))
                if content_type is None:
                    content_type = DEFAULT_CONTENT_TYPE

            # put_object natively handles multipart uploads
            result = self.client.put_object(
                bucket_name=bucket,
                object_name=object_name,
                data=file_obj,
                length=file_size,
                content_type=content_type or DEFAULT_CONTENT_TYPE,
                part_size=part_size,
                num_parallel_uploads=num_parallel_uploads
            )

            return {
                "object_name": object_name,
                "etag": result.etag,
                "size": file_size,
                "version_id": result.version_id
            }
        except S3Error as e:
            raise ValueError(f"Failed to upload file: {e.message}")
        finally:
            if should_close:
                file_obj.close()

    def upload_folder(
            self,
            folder_path: Union[str, Path],
            object_prefix: str = "",
            bucket_name: Optional[str] = None,
            part_size: int = 0,
            num_parallel_uploads: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Upload a folder and all its contents recursively

        Args:
            folder_path: Local folder path to upload
            object_prefix: Prefix for object names in storage (e.g., "backups/2024/")
            bucket_name: Bucket name (uses default if not provided)
            part_size: Size of each multipart chunk in bytes for large files
            num_parallel_uploads: Number of concurrent parallel chunk uploads per file

        Returns:
            List of upload results for each file
        """
        folder_path = Path(folder_path)
        if not folder_path.is_dir():
            raise ValueError(f"Path is not a directory: {folder_path}")

        results = []

        # Walk through all files in the folder
        for file_path in folder_path.rglob("*"):
            if file_path.is_file():
                # Calculate relative path from folder_path
                relative_path = file_path.relative_to(folder_path)

                # Construct object name with prefix
                if object_prefix:
                    object_name = f"{object_prefix.rstrip('/')}/{relative_path.as_posix()}"
                else:
                    object_name = relative_path.as_posix()

                try:
                    # Pass the large file configuration to upload_file
                    result = self.upload_file(
                        file_path=file_path,
                        object_name=object_name,
                        bucket_name=bucket_name,
                        part_size=part_size,
                        num_parallel_uploads=num_parallel_uploads
                    )
                    results.append(result)
                except ValueError as e:
                    results.append({
                        "object_name": object_name,
                        "error": str(e)
                    })

        return results
    
    def download_file(
        self,
        object_name: str,
        file_path: Optional[Union[str, Path]] = None,
        bucket_name: Optional[str] = None
    ) -> Union[bytes, Path]:
        """
        Download a file from storage

        Args:
            object_name: Object name in storage (e.g., "documents/file.txt")
            file_path: Local file path to save to (if None, returns bytes)
            bucket_name: Bucket name (uses default if not provided)

        Returns:
            If file_path is provided, returns the Path object.
            If file_path is None, returns the file content as bytes.
        """
        bucket = self._get_bucket(bucket_name)

        try:
            response = self.client.get_object(bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()

            if file_path is not None:
                # Save to file
                file_path = Path(file_path)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(data)
                return file_path
            else:
                # Return bytes
                return data
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise FileNotFoundError(f"File not found: {object_name}")
            raise ValueError(f"Failed to download file: {e.message}")

    def download_folder(
        self,
        folder_path: str,
        local_path: Union[str, Path],
        bucket_name: Optional[str] = None,
        recursive: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Download a folder from storage to a local directory.

        Lists all objects under the folder prefix, downloads each file (skips
        folder markers), and preserves the directory structure under local_path.

        Args:
            folder_path: Path to the folder in storage (e.g., "documents/" or "backups/2024/")
            local_path: Local directory to save files to
            bucket_name: Bucket name (uses default if not provided)
            recursive: Whether to download recursively (default: True)

        Returns:
            List of result dicts per file: {"object_name", "local_path", "size"}
            or {"object_name", "error"} on failure.
        """
        bucket = self._get_bucket(bucket_name)
        local_path = Path(local_path)
        local_path.mkdir(parents=True, exist_ok=True)

        prefix = folder_path.rstrip("/") + "/" if folder_path else ""
        results = []

        try:
            objects = self.client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=recursive
            )
            for obj in objects:
                if obj.object_name.endswith("/"):
                    continue
                rel = obj.object_name[len(prefix):] if prefix else obj.object_name
                target = local_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.download_file(
                        object_name=obj.object_name,
                        file_path=target,
                        bucket_name=bucket
                    )
                    results.append({
                        "object_name": obj.object_name,
                        "local_path": str(target),
                        "size": obj.size,
                    })
                except FileNotFoundError as e:
                    results.append({
                        "object_name": obj.object_name,
                        "error": str(e),
                    })
                except ValueError as e:
                    results.append({
                        "object_name": obj.object_name,
                        "error": str(e),
                    })
        except S3Error as e:
            raise ValueError(f"Failed to download folder: {e.message}")

        return results

    def delete_file(
        self,
        object_name: str,
        bucket_name: Optional[str] = None
    ) -> None:
        """
        Delete a single object from storage.

        Args:
            object_name: Object name in storage (e.g., "documents/file.txt")
            bucket_name: Bucket name (uses default if not provided)
        """
        bucket = self._get_bucket(bucket_name)
        try:
            self.client.remove_object(bucket_name=bucket, object_name=object_name)
        except S3Error as e:
            raise ValueError(f"Failed to delete file: {e.message}")

    def delete_folder(
        self,
        folder_path: str,
        bucket_name: Optional[str] = None,
        recursive: bool = True
    ) -> Dict[str, Any]:
        """
        Delete a folder and all objects under it from storage.

        Lists all objects under the folder prefix (including folder markers),
        then deletes them in batch.

        Args:
            folder_path: Path to the folder in storage (e.g., "documents/" or "backups/2024/")
            bucket_name: Bucket name (uses default if not provided)
            recursive: Whether to delete recursively (default: True)

        Returns:
            Dict with "deleted" (count) and "errors" (list of error messages).
        """
        bucket = self._get_bucket(bucket_name)
        prefix = folder_path.rstrip("/") + "/" if folder_path else ""

        object_names = []
        try:
            objects = self.client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=recursive
            )
            for obj in objects:
                object_names.append(obj.object_name)
        except S3Error as e:
            raise ValueError(f"Failed to list folder for deletion: {e.message}")

        deleted = 0
        errors = []
        if object_names:
            delete_list = [DeleteObject(name) for name in object_names]
            for err in self.client.remove_objects(
                bucket_name=bucket, delete_object_list=delete_list
            ):
                errors.append(str(err))
            deleted = len(object_names) - len(errors)

        return {"deleted": deleted, "errors": errors}

    def close(self):
        """Close the client (no-op for MinIO client)"""
        pass
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def get_storage_info() -> Dict[str, Any]:
    """
    Get storage information from the API.

    Calls the /storage_info endpoint to retrieve storage credentials and usage information.
    Uses environment variables for configuration:
    - PYROMIND_API_KEY: API key for authentication
    - PYROMIND_BASE_URL: Base URL for the API (optional)
    - PYROMIND_CLUSTER: Target cluster identifier (optional)

    Returns:
        Dictionary containing storage information:
        - access_key: Storage access key
        - secret_key: Storage secret key
        - url: Storage endpoint URL
        - bucket_name: User's bucket name
        - used_size: Used storage in bytes with unit (e.g., "2655304097792byte")
        - human_used_size: Human-readable used size (e.g., "2.41TB")
        - total_size: Total storage quota in bytes with unit (e.g., "7696581394432byte")
        - human_total_size: Human-readable total size (e.g., "7.00TB")

    Raises:
        PyroMindAPIError: If the API request fails
        ValueError: If API key is not available
    """
    api_key = os.getenv(ENV_API_KEY)
    if not api_key:
        raise ValueError(
            f"API key is required. Please set the {ENV_API_KEY} environment variable."
        )

    base_url = os.getenv(ENV_BASE_URL, DEFAULT_API_BASE_URL)
    cluster = os.getenv(ENV_CLUSTER, DEFAULT_CLUSTER)

    base_url = base_url.rstrip('/')
    url = f"{base_url}/storage_info"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Cluster": cluster,
    }

    try:
        response = requests.post(url, headers=headers, timeout=30)
        response.raise_for_status()

        result = response.json()
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result

    except requests.exceptions.HTTPError as e:
        error_data = {}
        try:
            error_data = e.response.json()
        except Exception:
            error_data = {"message": e.response.text if e.response else str(e)}

        error_message = error_data.get("message", str(e))
        raise PyroMindAPIError(
            message=f"Failed to get storage info: {error_message}",
            status_code=e.response.status_code if e.response else None,
            response=error_data
        )
    except requests.exceptions.RequestException as e:
        raise PyroMindAPIError(
            message=f"Failed to get storage info: {type(e).__name__}: {str(e)}",
            status_code=None
        )