"""
Inference API Client

This module provides a client for managing inference jobs via the PyroMind API.
"""

from typing import List
from .base import PyroMindClient
from .models import (
    InferenceJobRequest,
    InferenceJobResponse,
)


class InferenceClient(PyroMindClient):
    """
    Client for managing inference jobs
    
    Provides methods for creating, listing, getting, and deleting inference jobs.
    """
    
    def list(self) -> List[InferenceJobResponse]:
        """
        List all inference jobs
        
        Returns:
            List of InferenceJobResponse objects
        """
        response = self.get("/inference")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Handle different possible data structures
        if isinstance(data, dict) and "inference_jobs" in data:
            jobs_data = data["inference_jobs"]
        elif isinstance(data, dict) and "pagination" in data:
            # Response format: {inference_jobs: [...], pagination: {...}}
            jobs_data = data.get("inference_jobs", [])
        elif isinstance(data, list):
            jobs_data = data
        else:
            jobs_data = []
        
        # Convert each job data to InferenceJobResponse
        return [InferenceJobResponse(**job) if isinstance(job, dict) else job for job in jobs_data]
    
    def create(self, request: InferenceJobRequest) -> str:
        """
        Create a new inference job
        
        Args:
            request: InferenceJobCreateRequest with job configuration
            
        Returns:
            Job ID string
        """
        response = self.post("/inference", json_data=request.model_dump())
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns either {job_id: "..."} or full job object
        if isinstance(data, dict) and "job_id" in data:
            return data["job_id"]
        elif isinstance(data, dict) and "id" in data:
            return data["id"]
        else:
            # Try to parse as InferenceJobResponse and extract ID
            job_response = InferenceJobResponse(**data)
            return job_response.id
    
    def get_job(self, job_id: str) -> InferenceJobResponse:
        """
        Get a specific inference job by ID
        
        Args:
            job_id: ID of the inference job to retrieve
            
        Returns:
            InferenceJobResponse object
        """
        response = self.get(f"/inference/{job_id}")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        # Backend returns the job data directly in the data field
        return InferenceJobResponse(**data)
    
    def update(self, job_id: str, request: InferenceJobRequest) -> InferenceJobResponse:
        """
        Update an inference job
        
        Args:
            job_id: ID of the inference job to update
            request: InferenceJobRequest with updated configuration
            
        Returns:
            InferenceJobResponse object
        """
        if not isinstance(request, InferenceJobRequest):
            request = InferenceJobRequest(**request)
        
        request_dict = request.model_dump(exclude_none=True)
        
        response = self.put(f"/inference/{job_id}", json_data=request_dict)
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        return InferenceJobResponse(**data)
    
    def delete(self, job_id: str) -> None:
        """
        Delete an inference job
        
        Args:
            job_id: ID of the inference job to delete
        """
        self._request("DELETE", f"/inference/{job_id}")
    
    def pause(self, job_id: str) -> InferenceJobResponse:
        """
        Pause an inference job
        
        Args:
            job_id: ID of the inference job to pause
            
        Returns:
            InferenceJobResponse object
        """
        response = self.post(f"/inference/{job_id}/pause")
        # API returns {success: True, data: {...}} format
        data = self._extract_data(response)
        
        return InferenceJobResponse(**data)
    
    def get_framework(self) -> List[str]:
        """
        Get the list of inference frameworks
        
        Returns:
            List of framework names
        """
        response = self.get("/inference/get_framework")
        # API returns {success: True, data: {frameworks: [...]}, metadata: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "frameworks" in data:
            return data["frameworks"]
        return []
    
    def get_inf_image(self, framework: str) -> List[str]:
        """
        Get the list of inference images for a specific framework
        
        Args:
            framework: The framework name to get images for
            
        Returns:
            List of image names
        """
        response = self.get("/inference/get_inf_image", params={"framework": framework})
        # API returns {success: True, data: {images: [...]}, metadata: {...}} format
        data = self._extract_data(response)
        
        if isinstance(data, dict) and "images" in data:
            return data["images"]
        return []
