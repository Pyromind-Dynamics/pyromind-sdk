"""
Async EchoMind API Client

This module provides an async client for managing EchoMind instances via the PyroMind API.
"""

from typing import List, Optional
from .async_base import PyroMindAsyncClient
from .models import (
    EchoMindJobRequest,
    EchoMindJobResponse,
    EchoMindJobListAPIResponse,
    EchoMindPage,
    ListQuery,
    EchoMindJobAPIResponse,
    EchoMindJobCreateAPIResponse,
    InternalIPResponse,
)


class AsyncEchoMindClient(PyroMindAsyncClient):
    """
    Async client for managing EchoMind instances

    Provides async methods for creating, listing, getting, updating, pausing,
    resuming, and deleting EchoMind instances.
    """

    async def list(
        self,
        query: Optional[ListQuery] = None,
    ) -> List[EchoMindJobResponse]:
        """
        List all EchoMind instances (async)

        Returns:
            List of EchoMindJobResponse objects
        """
        query = query or ListQuery()
        jobs: List[EchoMindJobResponse] = []
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
    ) -> EchoMindPage:
        """Fetch one page of EchoMind instances with the total count (async)."""
        query = query or ListQuery()
        params = query.to_params(default_page_size=20)

        response = await self.get("/echomind", params=params)
        data = self._extract_data(response)

        if isinstance(data, dict):
            api_response = EchoMindJobListAPIResponse(**data)
            jobs_data = api_response.echomind_jobs
        elif isinstance(data, list):
            jobs_data = data
        else:
            jobs_data = []

        pagination = data.get("pagination") if isinstance(data, dict) else {}
        pagination = pagination if isinstance(pagination, dict) else {}
        total = int(pagination.get("total") or len(jobs_data) or 0)
        jobs = [EchoMindJobResponse(**job) if isinstance(job, dict) else job for job in jobs_data]
        return EchoMindPage(
            jobs=jobs,
            total=total,
            page_size=int(pagination.get("pageSize") or query.page_size or 20),
            page_num=int(pagination.get("pageNum") or query.page_num or 1),
        )

    async def create(self, request: EchoMindJobRequest) -> str:
        """
        Create a new EchoMind instance (async)

        Args:
            request: EchoMindJobRequest with instance configuration

        Returns:
            Job ID (str) of the created EchoMind instance
        """
        response = await self.post("/echomind", json_data=request.model_dump())
        data = self._extract_data(response)

        if isinstance(data, dict) and "job_id" in data:
            return data["job_id"]
        elif isinstance(data, dict):
            api_response = EchoMindJobCreateAPIResponse(**data)
            return api_response.job_id
        else:
            raise ValueError(f"Unexpected response format: {data}")

    async def get_job(self, job_id: str) -> EchoMindJobResponse:
        """
        Get a specific EchoMind instance by ID (async)

        Args:
            job_id: ID of the EchoMind instance to retrieve

        Returns:
            EchoMindJobResponse object
        """
        response = await self.get(f"/echomind/{job_id}")
        data = self._extract_data(response)

        if isinstance(data, dict) and "job" in data:
            job_data = data["job"]
        else:
            job_data = data

        api_response = EchoMindJobAPIResponse(job=job_data)
        return api_response.job

    async def get_internal_ip(self, job_id: str) -> InternalIPResponse:
        """
        Get the internal Pod IP of an EchoMind instance (async).

        Args:
            job_id: ID of the EchoMind instance to inspect

        Returns:
            InternalIPResponse containing the normalized resource ID and IP
        """
        response = await self.get(f"/echomind/{job_id}/internal_ip")
        data = self._extract_data(response)
        normalized = {
            "id": data.get("job_id") or data.get("id") or job_id,
            "internal_ip": data.get("internal_ip"),
        }
        return InternalIPResponse(**normalized)

    async def update(self, job_id: str, request: EchoMindJobRequest) -> EchoMindJobResponse:
        """
        Update an EchoMind instance configuration (async)

        Args:
            job_id: ID of the EchoMind instance to update
            request: EchoMindJobRequest with updated configuration

        Returns:
            EchoMindJobResponse object
        """
        response = await self.put(f"/echomind/{job_id}", json_data=request.model_dump())
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)

    async def delete(self, job_id: str) -> None:
        """
        Delete an EchoMind instance (async)

        Args:
            job_id: ID of the EchoMind instance to delete
        """
        await self._request("DELETE", f"/echomind/{job_id}")

    async def pause(self, job_id: str) -> EchoMindJobResponse:
        """
        Pause a running EchoMind instance (async)

        Args:
            job_id: ID of the EchoMind instance to pause

        Returns:
            EchoMindJobResponse object
        """
        response = await self.post(f"/echomind/{job_id}/pause")
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)

    async def resume(self, job_id: str) -> EchoMindJobResponse:
        """
        Resume a paused EchoMind instance (async)

        Args:
            job_id: ID of the EchoMind instance to resume

        Returns:
            EchoMindJobResponse object
        """
        response = await self.post(f"/echomind/{job_id}/resume")
        data = self._extract_data(response)

        return EchoMindJobResponse(**data)
