"""
Profile API Client

This module provides a client for managing user profile-related API operations.
"""

from typing import List

from .base import PyroMindClient
from .models import (
    ProfileAccessKeyResponse,
    ProfileStorageInfoResponse,
    ProfileUserInfoResponse,
    UserPubKey,
    UserPubKeyListResponse,
    UserPubKeyRequest,
)


class ProfileClient(PyroMindClient):
    """
    Client for managing profile-related operations

    Provides methods for fetching user info, access key, storage info,
    and managing SSH public keys.
    """

    def get_user_info(self, credit_info: bool = False) -> ProfileUserInfoResponse:
        """
        Get authenticated user information

        Args:
            credit_info: Whether to fetch credit-related fields

        Returns:
            ProfileUserInfoResponse object
        """
        response = self.post("/user_info", json_data={"credit_info": credit_info})
        data = self._extract_data(response)
        return ProfileUserInfoResponse(**data)

    def get_access_key(self) -> str:
        """
        Get the authenticated user's access key

        Returns:
            Access key string
        """
        response = self.post("/find_access_key")
        data = self._extract_data(response)
        return ProfileAccessKeyResponse(**data).access_key

    def get_storage_info(self) -> ProfileStorageInfoResponse:
        """
        Get the authenticated user's storage credentials

        Returns:
            ProfileStorageInfoResponse object
        """
        response = self.post("/storage_info")
        data = self._extract_data(response)
        return ProfileStorageInfoResponse(**data)

    def add_key(self, request: UserPubKeyRequest) -> bool:
        """
        Add an SSH public key for the authenticated user

        Args:
            request: UserPubKeyRequest containing the SSH key payload

        Returns:
            True when the request succeeds
        """
        if not isinstance(request, UserPubKeyRequest):
            request = UserPubKeyRequest(**request)

        self.post("/add_key", json_data=request.model_dump(exclude_none=True))
        return True

    def list_keys(self) -> List[UserPubKey]:
        """
        List SSH public keys for the authenticated user

        Returns:
            List of UserPubKey objects
        """
        response = self.post("/query_key")
        data = self._extract_data(response)
        return UserPubKeyListResponse(**data).keys
