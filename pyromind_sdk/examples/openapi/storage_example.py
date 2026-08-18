#!/usr/bin/env python3
"""
Storage Operations Example

This example demonstrates how to use the StorageClient for file operations.

The storage credentials can be provided via:
1. Environment variables (recommended):
   - PYROMIND_STORAGE_ENDPOINT
   - PYROMIND_API_KEY (used as access key for storage)
   - PYROMIND_STORAGE_SECRET_KEY
   - PYROMIND_STORAGE_BUCKET (optional)
2. Parameters when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import os
from pathlib import Path
from typing import Optional

from pyromind_sdk import StorageClient


def list_files_example(folder_path: str = "", bucket_name: Optional[str] = None) -> None:
    """Example: List files and folders in a directory."""
    storage = StorageClient()
    
    try:
        print(f"Listing files in '{folder_path}'...")
        items = storage.list_files(folder_path=folder_path, bucket_name=bucket_name, recursive=True, max_depth=2)
        
        files = [item for item in items if item.get("type") == "file"]
        folders = [item for item in items if item.get("type") == "folder"]
        
        print(f"✓ Found {len(items)} item(s) ({len(files)} file(s), {len(folders)} folder(s)):")
        for item in items:
            if item.get("type") == "folder":
                print(f"  - {item['object_name']} [FOLDER]")
            else:
                size_mb = item['size'] / (1024 * 1024)
                print(f"  - {item['object_name']} ({size_mb:.2f} MB)")
    except Exception as e:
        print(f"✗ Failed to list files: {e}")
    finally:
        storage.close()


def check_file_exists_example(file_path: str, bucket_name: Optional[str] = None) -> None:
    """Example: Check if a file exists."""
    storage = StorageClient()
    
    try:
        exists = storage.file_exists(file_path, bucket_name=bucket_name)
        if exists:
            print(f"✓ File exists: {file_path}")
        else:
            print(f"✗ File not found: {file_path}")
    except Exception as e:
        print(f"✗ Failed to check file existence: {e}")
    finally:
        storage.close()


def upload_file_example(
    local_file: str,
    object_name: str,
    bucket_name: Optional[str] = None
) -> None:
    """Example: Upload a file."""
    storage = StorageClient()
    
    try:
        print(f"Uploading {local_file} to {object_name}...")
        result = storage.upload_file(
            file_path=local_file,
            object_name=object_name,
            bucket_name=bucket_name
        )
        
        print(f"✓ File uploaded successfully!")
        print(f"  Object: {result['object_name']}")
        print(f"  Size: {result['size']} bytes")
        print(f"  ETag: {result['etag']}")
    except FileNotFoundError as e:
        print(f"✗ File not found: {e}")
    except Exception as e:
        print(f"✗ Failed to upload file: {e}")
    finally:
        storage.close()


def upload_folder_example(
    local_folder: str,
    object_prefix: str = "",
    bucket_name: Optional[str] = None
) -> None:
    """Example: Upload a folder."""
    storage = StorageClient()
    
    try:
        print(f"Uploading folder {local_folder} to {object_prefix}...")
        results = storage.upload_folder(
            folder_path=local_folder,
            object_prefix=object_prefix,
            bucket_name=bucket_name
        )
        
        success_count = sum(1 for r in results if "error" not in r)
        failed_count = len(results) - success_count
        
        print(f"✓ Upload completed!")
        print(f"  Success: {success_count} file(s)")
        print(f"  Failed: {failed_count} file(s)")
        
        if failed_count > 0:
            print("\nFailed files:")
            for result in results:
                if "error" in result:
                    print(f"  - {result['object_name']}: {result['error']}")
    except Exception as e:
        print(f"✗ Failed to upload folder: {e}")
    finally:
        storage.close()


def download_file_example(
    object_name: str,
    local_file: Optional[str] = None,
    bucket_name: Optional[str] = None
) -> None:
    """Example: Download a file."""
    storage = StorageClient()
    
    try:
        if local_file:
            print(f"Downloading {object_name} to {local_file}...")
            result = storage.download_file(
                object_name=object_name,
                file_path=local_file,
                bucket_name=bucket_name
            )
            print(f"✓ File downloaded to: {result}")
        else:
            print(f"Downloading {object_name} as bytes...")
            data = storage.download_file(
                object_name=object_name,
                bucket_name=bucket_name
            )
            print(f"✓ Downloaded {len(data)} bytes")
    except FileNotFoundError as e:
        print(f"✗ File not found: {e}")
    except Exception as e:
        print(f"✗ Failed to download file: {e}")
    finally:
        storage.close()


def main():
    """Main example function demonstrating storage operations."""
    print("=" * 60)
    print("Storage Operations Examples")
    print("=" * 60)
    
    # Example 1: List files
    print("\n1. Listing files...")
    list_files_example(folder_path="dist")
    
    # Example 2: Check file existence
    print("\n2. Checking file existence...")
    check_file_exists_example("dist/index.html")
    
    # Example 3: Upload a file (uncomment to test)
    # print("\n3. Uploading a file...")
    # upload_file_example(
    #     local_file="/Users/jiangwenchang/Downloads/Doubao_universal.dmg",
    #     object_name="/dist/tes00000011111.dmg"
    # )
    
    # Example 4: Upload a folder (uncomment to test)
    # print("\n4. Uploading a folder...")
    # upload_folder_example(
    #     local_folder="/Users/jiangwenchang/Downloads/",
    #     object_prefix="/dist",
    #
    # )
    
    # Example 5: Download a file (uncomment to test)
    # print("\n5. Downloading a file...")
    # download_file_example(
    #     object_name="documents/file.txt",
    #     local_file="/path/to/downloaded_file.txt"
    # )


#     client = StorageClient(access_key="X805271PGQEC8RDSNQNN", cluster="us-west-1#pre")
#     # client.upload_folder("/Users/jiangwenchang/Downloads/tarballs", "testDir/slime/examples/coding_agent_rl/tarballs/", bucket_name="1000001514", part_size=20 * 1024 * 1024, num_parallel_uploads=5)
#     client.upload_file("/Users/jiangwenchang/Downloads/tmax_train_full.jsonl", "testDir/slime/examples/coding_agent_rl/data/tmax_train_full.jsonl", bucket_name="1000001514", part_size=20 * 1024 * 1024, num_parallel_uploads=5)


if __name__ == "__main__":
    main()
