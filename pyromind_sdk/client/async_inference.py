"""
Async Inference API Client

This module provides an async client for managing inference jobs via the PyroMind API.
"""

from typing import List, Optional
from .async_base import PyroMindAsyncClient
from .models import (
    InferenceJobRequest,
    InferenceJobResponse,
    InferencePage,
    ListQuery,
    InternalIPResponse,
)


class AsyncInferenceClient(PyroMindAsyncClient):
    """
    Async client for managing inference jobs

    Provides async methods for creating, listing, getting, and deleting inference jobs.
    """

    async def list(
        self,
        query: Optional[ListQuery] = None,
    ) -> List[InferenceJobResponse]:
        """
        List all inference jobs (async)

        Returns:
            List of InferenceJobResponse objects
        """
        query = query or ListQuery()
        jobs: List[InferenceJobResponse] = []
        current_page = 1
        page_size = query.page_size or 20
        while True:
            page = await self.list_page(
                query.model_copy(update={"page_size": page_size, "page_num": current_page})
            )
            jobs.extend(page.jobs)
            if not page.jobs or current_page * page_size >= page.total:
                break
            current_page += 1
        return jobs

    async def list_page(
        self,
        query: Optional[ListQuery] = None,
    ) -> InferencePage:
        """Fetch one page of inference jobs with the total count (async)."""
        query = query or ListQuery()
        params = query.to_params(default_page_size=20)

        response = await self.get("/inference", params=params)
        data = self._extract_data(response)

        if isinstance(data, dict) and "inference_jobs" in data:
            jobs_data = data["inference_jobs"]
        elif isinstance(data, dict) and "pagination" in data:
            jobs_data = data.get("inference_jobs", [])
        elif isinstance(data, list):
            jobs_data = data
        else:
            jobs_data = []

        pagination = data.get("pagination") if isinstance(data, dict) else {}
        pagination = pagination if isinstance(pagination, dict) else {}
        total = int(pagination.get("total") or len(jobs_data) or 0)

        jobs = [InferenceJobResponse(**job) if isinstance(job, dict) else job for job in jobs_data]
        return InferencePage(
            jobs=jobs,
            total=total,
            page_size=int(pagination.get("pageSize") or query.page_size or 20),
            page_num=int(pagination.get("pageNum") or query.page_num or 1),
        )

    async def create(self, request: InferenceJobRequest) -> str:
        """
        Create a new inference job (async)

        Args:
            request: InferenceJobCreateRequest with job configuration

        Returns:
            Job ID string
        """
        response = await self.post("/inference", json_data=request.model_dump())
        data = self._extract_data(response)

        if isinstance(data, dict) and "job_id" in data:
            return data["job_id"]
        elif isinstance(data, dict) and "id" in data:
            return data["id"]
        else:
            job_response = InferenceJobResponse(**data)
            return job_response.id

    async def get_job(self, job_id: str) -> InferenceJobResponse:
        """
        Get a specific inference job by ID (async)

        Args:
            job_id: ID of the inference job to retrieve

        Returns:
            InferenceJobResponse object
        """
        response = await self.get(f"/inference/{job_id}")
        data = self._extract_data(response)

        return InferenceJobResponse(**data)

    async def get_internal_ip(self, job_id: str) -> InternalIPResponse:
        """
        Get the internal Pod IP of an inference job (async).

        Args:
            job_id: ID of the inference job to inspect

        Returns:
            InternalIPResponse containing the normalized resource ID and IP
        """
        response = await self.get(f"/inference/{job_id}/internal_ip")
        data = self._extract_data(response)
        normalized = {
            "id": data.get("job_id") or data.get("id") or job_id,
            "internal_ip": data.get("internal_ip"),
        }
        return InternalIPResponse(**normalized)

    async def update(self, job_id: str, request: InferenceJobRequest) -> InferenceJobResponse:
        """
        Update an inference job (async)

        Args:
            job_id: ID of the inference job to update
            request: InferenceJobRequest with updated configuration

        Returns:
            InferenceJobResponse object
        """
        if not isinstance(request, InferenceJobRequest):
            request = InferenceJobRequest(**request)

        request_dict = request.model_dump(exclude_none=True)

        response = await self.put(f"/inference/{job_id}", json_data=request_dict)
        data = self._extract_data(response)

        return InferenceJobResponse(**data)

    async def delete(self, job_id: str) -> None:
        """
        Delete an inference job (async)

        Args:
            job_id: ID of the inference job to delete
        """
        await self._request("DELETE", f"/inference/{job_id}")

    async def pause(self, job_id: str) -> InferenceJobResponse:
        """
        Pause an inference job (async)

        Args:
            job_id: ID of the inference job to pause

        Returns:
            InferenceJobResponse object
        """
        response = await self.post(f"/inference/{job_id}/pause")
        data = self._extract_data(response)

        return InferenceJobResponse(**data)

    async def get_framework(self) -> List[str]:
        """
        Get the list of inference frameworks (async)

        Returns:
            List of framework names
        """
        response = await self.get("/inference/get_framework")
        data = self._extract_data(response)

        if isinstance(data, dict) and "frameworks" in data:
            return data["frameworks"]
        return []

    async def get_inf_image(self, framework: str) -> List[str]:
        """
        Get the list of inference images for a specific framework (async)

        Args:
            framework: The framework name to get images for

        Returns:
            List of image names
        """
        response = await self.get("/inference/get_inf_image", params={"framework": framework})
        data = self._extract_data(response)

        if isinstance(data, dict) and "images" in data:
            return data["images"]
        return []
