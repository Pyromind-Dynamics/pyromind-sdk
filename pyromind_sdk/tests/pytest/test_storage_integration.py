#!/usr/bin/env python3
"""
Integration tests for StorageClient

This module provides pytest-based integration tests for the StorageClient,
using real MinIO/S3 storage operations (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for storage access_key
- PYROMIND_STORAGE_SECRET_KEY: Secret key for storage authentication
- PYROMIND_STORAGE_BUCKET: Bucket name (optional)

These tests will create, upload, download, and delete actual storage objects.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

from pyromind_sdk import StorageClient


class TestStorageClientBasics:
    """Test basic storage operations: list_files, file_exists"""

    def test_list_files(self, storage_client: StorageClient, test_prefix: str):
        """Test listing files in a folder"""
        # Create a test folder with some files
        folder_name = f"{test_prefix}_list_files"

        # Upload a few test files
        test_files = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                file_path = Path(tmpdir) / f"test_file_{i}.txt"
                file_path.write_text(f"Test content {i}")
                object_name = f"{folder_name}/file_{i}.txt"
                storage_client.upload_file(file_path, object_name)
                test_files.append(object_name)

        try:
            # List files in the folder
            files = storage_client.list_files(folder_name)

            # Verify results
            assert isinstance(files, list), "list_files should return a list"
            assert len(files) >= 3, f"Expected at least 3 files, got {len(files)}"

            # Check that our uploaded files are in the list
            file_names = [f["object_name"] for f in files]
            for test_file in test_files:
                assert test_file in file_names, f"Expected file {test_file} not found in list"

            # Verify structure of returned items
            for file_info in files:
                assert "object_name" in file_info, "File info missing 'object_name'"
                assert "size" in file_info, "File info missing 'size'"
                assert "last_modified" in file_info, "File info missing 'last_modified'"
                assert "etag" in file_info, "File info missing 'etag'"
                assert "type" in file_info, "File info missing 'type'"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_file_exists(self, storage_client: StorageClient, test_prefix: str):
        """Test checking if a file exists"""
        # Upload a test file
        folder_name = f"{test_prefix}_file_exists"
        object_name = f"{folder_name}/test_file.txt"

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_file.txt"
            file_path.write_text("Test content for exists check")
            storage_client.upload_file(file_path, object_name)

        try:
            # Check that the file exists
            exists = storage_client.file_exists(object_name)
            assert exists is True, f"File {object_name} should exist but file_exists returned False"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_file_exists_missing(self, storage_client: StorageClient, test_prefix: str):
        """Test checking if a non-existent file exists"""
        # Check a file that doesn't exist
        object_name = f"{test_prefix}_nonexistent/file.txt"
        exists = storage_client.file_exists(object_name)
        assert exists is False, f"File {object_name} should not exist but file_exists returned True"


class TestStorageClientUploadDownload:
    """Test file upload and download operations"""

    def test_upload_file_from_path(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a file from a local path"""
        folder_name = f"{test_prefix}_upload_path"

        # Create a test file
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "upload_test.txt"
            test_content = "Test content for upload from path"
            file_path.write_text(test_content)

            # Upload the file
            object_name = f"{folder_name}/uploaded_file.txt"
            result = storage_client.upload_file(file_path, object_name)

        try:
            # Verify upload result
            assert isinstance(result, dict), "Upload result should be a dictionary"
            assert "object_name" in result, "Upload result missing 'object_name'"
            assert "etag" in result, "Upload result missing 'etag'"
            assert "size" in result, "Upload result missing 'size'"
            assert result["object_name"] == object_name, f"Object name mismatch: {result['object_name']} != {object_name}"
            assert result["size"] == len(test_content), f"Size mismatch: {result['size']} != {len(test_content)}"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_upload_file_from_bytes(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a file from bytes (file-like object)"""
        folder_name = f"{test_prefix}_upload_bytes"
        object_name = f"{folder_name}/bytes_file.bin"

        # Create test content as bytes
        test_content = b"Test binary content \x00\x01\x02\x03"

        # Upload using BytesIO
        from io import BytesIO
        file_obj = BytesIO(test_content)

        result = storage_client.upload_file(file_obj, object_name)

        try:
            # Verify upload result
            assert isinstance(result, dict), "Upload result should be a dictionary"
            assert result["object_name"] == object_name, f"Object name mismatch"
            assert result["size"] == len(test_content), f"Size mismatch: {result['size']} != {len(test_content)}"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_download_file_to_path(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a file to a local path"""
        folder_name = f"{test_prefix}_download_path"
        object_name = f"{folder_name}/download_test.txt"
        test_content = "Test content for download to path"

        # First upload a file
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "upload.txt"
            upload_path.write_text(test_content)
            storage_client.upload_file(upload_path, object_name)

        try:
            # Download the file
            with tempfile.TemporaryDirectory() as tmpdir:
                download_path = Path(tmpdir) / "downloaded.txt"
                result_path = storage_client.download_file(object_name, download_path)

                # Verify download
                assert result_path == download_path, f"Download path mismatch: {result_path} != {download_path}"
                assert download_path.exists(), "Downloaded file does not exist"

                # Verify content
                downloaded_content = download_path.read_text()
                assert downloaded_content == test_content, f"Content mismatch: {downloaded_content} != {test_content}"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_download_file_to_bytes(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a file as bytes"""
        folder_name = f"{test_prefix}_download_bytes"
        object_name = f"{folder_name}/bytes_test.txt"
        test_content = "Test content for download as bytes"

        # First upload a file
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "upload.txt"
            upload_path.write_text(test_content)
            storage_client.upload_file(upload_path, object_name)

        try:
            # Download as bytes
            downloaded_bytes = storage_client.download_file(object_name)

            # Verify download
            assert isinstance(downloaded_bytes, bytes), f"Downloaded data should be bytes, got {type(downloaded_bytes)}"
            assert downloaded_bytes.decode("utf-8") == test_content, f"Content mismatch"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)

    def test_roundtrip_integrity(self, storage_client: StorageClient, test_prefix: str):
        """Test that uploaded and downloaded content matches exactly"""
        folder_name = f"{test_prefix}_roundtrip"
        object_name = f"{folder_name}/integrity_test.bin"

        # Create test content with various byte values
        test_content = bytes(range(256)) * 10  # 2560 bytes with all possible byte values

        # Upload
        from io import BytesIO
        storage_client.upload_file(BytesIO(test_content), object_name)

        try:
            # Download
            downloaded_bytes = storage_client.download_file(object_name)

            # Verify integrity
            assert downloaded_bytes == test_content, "Roundtrip content mismatch - data corruption detected"
            assert len(downloaded_bytes) == len(test_content), f"Length mismatch: {len(downloaded_bytes)} != {len(test_content)}"

            # Also verify checksum
            original_hash = hashlib.sha256(test_content).hexdigest()
            downloaded_hash = hashlib.sha256(downloaded_bytes).hexdigest()
            assert original_hash == downloaded_hash, f"SHA256 mismatch: {original_hash} != {downloaded_hash}"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)


class TestStorageClientFolderOperations:
    """Test folder operations: upload_folder, download_folder, delete_folder, list_files with max_depth"""

    def test_upload_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading a folder with nested structure"""
        remote_prefix = f"{test_prefix}_upload_folder"

        # Create a local folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            local_folder = Path(tmpdir) / "test_folder"
            local_folder.mkdir()

            # Create files at different levels
            (local_folder / "root_file.txt").write_text("Root level file")
            (local_folder / "root_file2.txt").write_text("Root level file 2")

            # Create subfolder with files
            subfolder = local_folder / "subfolder"
            subfolder.mkdir()
            (subfolder / "nested_file.txt").write_text("Nested file")

            # Create deeper subfolder
            deep_folder = subfolder / "deep_folder"
            deep_folder.mkdir()
            (deep_folder / "deep_file.txt").write_text("Deep nested file")

            # Upload the folder
            results = storage_client.upload_folder(local_folder, remote_prefix)

        try:
            # Verify upload results
            assert isinstance(results, list), "upload_folder should return a list"
            assert len(results) >= 4, f"Expected at least 4 uploaded files, got {len(results)}"

            # Check that all uploads succeeded (no errors)
            for result in results:
                assert "error" not in result, f"Upload failed for {result.get('object_name')}: {result.get('error')}"
                assert "object_name" in result, "Upload result missing 'object_name'"

            # Verify files exist in storage
            all_files = storage_client.list_files(remote_prefix)
            file_names = [f["object_name"] for f in all_files if f["type"] == "file"]
            assert len(file_names) >= 4, f"Expected at least 4 files in storage, got {len(file_names)}"
        finally:
            # Clean up
            storage_client.delete_folder(remote_prefix)

    def test_download_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a folder"""
        remote_prefix = f"{test_prefix}_download_folder"

        # First upload a folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            local_folder = Path(tmpdir) / "source"
            local_folder.mkdir()

            (local_folder / "file1.txt").write_text("Content 1")
            (local_folder / "file2.txt").write_text("Content 2")

            subfolder = local_folder / "sub"
            subfolder.mkdir()
            (subfolder / "file3.txt").write_text("Content 3")

            storage_client.upload_folder(local_folder, remote_prefix)

        try:
            # Download the folder
            with tempfile.TemporaryDirectory() as tmpdir:
                download_target = Path(tmpdir) / "downloaded"
                results = storage_client.download_folder(remote_prefix, download_target)

                # Verify download results
                assert isinstance(results, list), "download_folder should return a list"
                assert len(results) >= 3, f"Expected at least 3 downloaded files, got {len(results)}"

                # Check that all downloads succeeded
                for result in results:
                    assert "error" not in result, f"Download failed for {result.get('object_name')}: {result.get('error')}"
                    assert "object_name" in result, "Download result missing 'object_name'"
                    assert "local_path" in result, "Download result missing 'local_path'"

                # Verify files were downloaded correctly
                assert download_target.exists(), "Download target directory does not exist"
                assert (download_target / "file1.txt").exists(), "file1.txt not downloaded"
                assert (download_target / "file2.txt").exists(), "file2.txt not downloaded"
                assert (download_target / "sub" / "file3.txt").exists(), "sub/file3.txt not downloaded"

                # Verify content
                assert (download_target / "file1.txt").read_text() == "Content 1"
                assert (download_target / "file2.txt").read_text() == "Content 2"
                assert (download_target / "sub" / "file3.txt").read_text() == "Content 3"
        finally:
            # Clean up
            storage_client.delete_folder(remote_prefix)

    def test_delete_folder(self, storage_client: StorageClient, test_prefix: str):
        """Test deleting a folder and all its contents"""
        folder_name = f"{test_prefix}_delete_folder"

        # Create a folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            local_folder = Path(tmpdir) / "to_delete"
            local_folder.mkdir()

            (local_folder / "file1.txt").write_text("File 1")
            (local_folder / "file2.txt").write_text("File 2")

            subfolder = local_folder / "sub"
            subfolder.mkdir()
            (subfolder / "file3.txt").write_text("File 3")

            storage_client.upload_folder(local_folder, folder_name)

        # Verify files exist before deletion
        files_before = storage_client.list_files(folder_name)
        file_count_before = len([f for f in files_before if f["type"] == "file"])
        assert file_count_before >= 3, f"Expected at least 3 files before deletion, got {file_count_before}"

        # Delete the folder
        result = storage_client.delete_folder(folder_name)

        # Verify deletion result
        assert isinstance(result, dict), "delete_folder should return a dict"
        assert "deleted" in result, "Delete result missing 'deleted' count"
        assert "errors" in result, "Delete result missing 'errors' list"
        assert result["deleted"] >= 3, f"Expected at least 3 deleted items, got {result['deleted']}"
        assert len(result["errors"]) == 0, f"Deletion errors occurred: {result['errors']}"

        # Verify files no longer exist
        files_after = storage_client.list_files(folder_name)
        assert len(files_after) == 0, f"Expected empty folder after deletion, found {len(files_after)} items"

    def test_list_files_with_max_depth(self, storage_client: StorageClient, test_prefix: str):
        """Test listing files with max_depth parameter"""
        folder_name = f"{test_prefix}_max_depth"

        # Create a nested folder structure
        with tempfile.TemporaryDirectory() as tmpdir:
            local_folder = Path(tmpdir) / "nested"
            local_folder.mkdir()

            # Level 0: root file
            (local_folder / "root.txt").write_text("Root file")

            # Level 1: subfolder with file
            level1 = local_folder / "level1"
            level1.mkdir()
            (level1 / "level1.txt").write_text("Level 1 file")

            # Level 2: deeper subfolder with file
            level2 = level1 / "level2"
            level2.mkdir()
            (level2 / "level2.txt").write_text("Level 2 file")

            # Level 3: even deeper
            level3 = level2 / "level3"
            level3.mkdir()
            (level3 / "level3.txt").write_text("Level 3 file")

            storage_client.upload_folder(local_folder, folder_name)

        try:
            # List with max_depth=0 (should only show direct children)
            files_depth0 = storage_client.list_files(folder_name, recursive=True, max_depth=0)
            file_names_depth0 = [f["object_name"] for f in files_depth0 if f["type"] == "file"]

            # Should only see root.txt and the level1 folder marker (but not level1.txt)
            assert any("root.txt" in name for name in file_names_depth0), "root.txt should be in depth 0 listing"
            # Verify level1.txt is NOT in the listing
            assert not any("level1.txt" in name for name in file_names_depth0), "level1.txt should NOT be in depth 0 listing"
            assert not any("level2.txt" in name for name in file_names_depth0), "level2.txt should NOT be in depth 0 listing"

            # List with max_depth=1 (should see root.txt, level1 folder, and level1.txt)
            files_depth1 = storage_client.list_files(folder_name, recursive=True, max_depth=1)
            file_names_depth1 = [f["object_name"] for f in files_depth1 if f["type"] == "file"]

            # Should see level1.txt but NOT level2.txt or level3.txt
            assert any("level1.txt" in name for name in file_names_depth1), "level1.txt should be in depth 1 listing"
            assert not any("level2.txt" in name for name in file_names_depth1), "level2.txt should NOT be in depth 1 listing"
            assert not any("level3.txt" in name for name in file_names_depth1), "level3.txt should NOT be in depth 1 listing"

            # List without max_depth (should see all files)
            files_all = storage_client.list_files(folder_name, recursive=True, max_depth=None)
            file_names_all = [f["object_name"] for f in files_all if f["type"] == "file"]

            # Should see all files
            assert any("root.txt" in name for name in file_names_all), "root.txt should be in full listing"
            assert any("level1.txt" in name for name in file_names_all), "level1.txt should be in full listing"
            assert any("level2.txt" in name for name in file_names_all), "level2.txt should be in full listing"
            assert any("level3.txt" in name for name in file_names_all), "level3.txt should be in full listing"

            # Verify max_depth actually limits the results
            assert len(file_names_depth0) < len(file_names_all), "Depth 0 should have fewer files than full listing"
            assert len(file_names_depth1) < len(file_names_all), "Depth 1 should have fewer files than full listing"
        finally:
            # Clean up
            storage_client.delete_folder(folder_name)


class TestStorageClientErrors:
    """Test error handling for various edge cases"""

    def test_download_nonexistent_file(self, storage_client: StorageClient, test_prefix: str):
        """Test downloading a file that doesn't exist"""
        object_name = f"{test_prefix}_nonexistent/file.txt"

        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError) as exc_info:
            storage_client.download_file(object_name)

        assert object_name in str(exc_info.value), f"Error message should mention {object_name}"

    def test_upload_invalid_path(self, storage_client: StorageClient, test_prefix: str):
        """Test uploading from a non-existent local path"""
        # Create a non-existent path
        invalid_path = Path("/tmp/nonexistent_path_12345/test.txt")

        object_name = f"{test_prefix}_invalid_path/upload.txt"

        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError) as exc_info:
            storage_client.upload_file(invalid_path, object_name)

        assert "File not found" in str(exc_info.value), "Error message should mention file not found"
