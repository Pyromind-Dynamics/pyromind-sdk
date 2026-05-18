"""
EchoMind API Client

This module provides a client for managing EchoMind instances via the PyroMind API.
"""

from typing import List
from .base import PyroMindClient
from .models import (
    EchoMindJobRequest,
    EchoMindJobResponse,
    EchoMindJobListAPIResponse,
    EchoMindJobAPIResponse,
    EchoMindJobCreateAPIResponse,
)


class EchoMindClient(PyroMindClient):
    """
    Client for managing EchoMind instances

    Provides methods for creating, listing, getting, updating, pausing,
    resuming, and deleting EchoMind instances.
    """

    def list(self) -> List[EchoMindJobResponse]:
        """
        List all EchoMind instances

        Returns:
            List of EchoMindJobResponse objects
        """
        response = self.get("/echomind")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        # Handle different possible data structures
        if isinstance(data, dict):
            # API returns {echomind_jobs: [], page: 0, ...} format
            api_response = EchoMindJobListAPIResponse(**data)
            return api_response.echomind_jobs
        elif isinstance(data, list):
            # Fallback: if data is directly a list
            api_response = EchoMindJobListAPIResponse(echomind_jobs=data)
            return api_response.echomind_jobs
        else:
            return []

    def create(self, request: EchoMindJobRequest) -> str:
        """
        Create a new EchoMind instance

        Args:
            request: EchoMindJobRequest with instance configuration

        Returns:
            Job ID (str) of the created EchoMind instance
        """
        response = self.post("/echomind", json_data=request.model_dump())
        # API returns {success: True, data: {job_id: "..."}} format
        data = self._extract_data(response)

        if isinstance(data, dict) and "job_id" in data:
            return data["job_id"]
        elif isinstance(data, dict):
            # Try to parse as EchoMindJobCreateAPIResponse
            api_response = EchoMindJobCreateAPIResponse(**data)
            return api_response.job_id
        else:
            raise ValueError(f"Unexpected response format: {data}")

    def get_job(self, job_id: str) -> EchoMindJobResponse:
        """
        Get a specific EchoMind instance by ID

        Args:
            job_id: ID of the EchoMind instance to retrieve

        Returns:
            EchoMindJobResponse object
        """
        response = self.get(f"/echomind/{job_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        if isinstance(data, dict) and "job" in data:
            job_data = data["job"]
        else:
            job_data = data

        api_response = EchoMindJobAPIResponse(job=job_data)
        return api_response.job

    def update(self, job_id: str, request: EchoMindJobRequest) -> EchoMindJobResponse:
        """
        Update an EchoMind instance configuration

        Args:
            job_id: ID of the EchoMind instance to update
            request: EchoMindJobRequest with updated configuration

        Returns:
            EchoMindJobResponse object
        """
        response = self.put(f"/echomind/{job_id}", json_data=request.model_dump())
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)

    def delete(self, job_id: str) -> None:
        """
        Delete an EchoMind instance

        Args:
            job_id: ID of the EchoMind instance to delete
        """
        self._request("DELETE", f"/echomind/{job_id}")

    def pause(self, job_id: str) -> EchoMindJobResponse:
        """
        Pause a running EchoMind instance

        Args:
            job_id: ID of the EchoMind instance to pause

        Returns:
            EchoMindJobResponse object
        """
        response = self.post(f"/echomind/{job_id}/pause")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)

    def resume(self, job_id: str) -> EchoMindJobResponse:
        """
        Resume a paused EchoMind instance

        Args:
            job_id: ID of the EchoMind instance to resume

        Returns:
            EchoMindJobResponse object
        """
        response = self.post(f"/echomind/{job_id}/resume")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)
