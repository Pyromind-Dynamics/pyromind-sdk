"""
Async Base client class for PyroMind API

This module provides the async base client class that handles authentication,
HTTP requests, and error handling for all async API clients.
"""

import os
import logging
from contextlib import asynccontextmanager
import asyncio
import aiohttp
from typing import Optional, Dict, Any

from .base import append_trace_id, extract_trace_id
from .trace import (
    TRACE_ID_HEADER,
    current_trace_id,
    ensure_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)


# Constants
DEFAULT_API_BASE_URL = "https://api-portal.pyromind.ai/api/v1"
DEFAULT_CLUSTER = "us-west-2"
DEFAULT_TIMEOUT = 600
DEFAULT_MAX_RETRIES = 3
DEFAULT_CONNECTOR_LIMIT = 256
ENV_API_KEY = "PYROMIND_API_KEY"
ENV_BASE_URL = "PYROMIND_BASE_URL"
ENV_CLUSTER = "PYROMIND_CLUSTER"
ENV_LOG_FORMAT = "PYROMIND_LOG_FORMAT"
ENV_LOG_LEVEL = "PYROMIND_LOG_LEVEL"
RETRY_STATUS_CODES = [502, 503, 504]

#: ``Accept-Encoding`` sent with every async request.
#:
#: aiohttp advertises ``br`` on its own as soon as the ``brotli`` package is
#: importable, and its streaming brotli decoder then fails on what the PyroMind
#: edge actually returns: a **200** whose body is perfectly valid brotli (curl
#: and ``brotli.decompress()`` both decode it) comes back as
#: ``ClientPayloadError: Can not decode content-encoding: br`` — reproduced on
#: aiohttp 3.14.3 with brotli 1.2.0. That error also carries a misleading
#: "400" (aiohttp reports ``ContentEncodingError`` as a 400-class exception),
#: which makes it look like a server rejection when it is purely client-side.
#:
#: gzip gets the same compression benefit without the broken path, so that is
#: what we ask for. Opt back in with
#: ``PYROMIND_ACCEPT_ENCODING="br, gzip, deflate"`` once aiohttp's br decoding
#: is known good for the installed pair.
DEFAULT_ACCEPT_ENCODING = "gzip, deflate"
ENV_ACCEPT_ENCODING = "PYROMIND_ACCEPT_ENCODING"

ERROR_MESSAGE_MAX_LENGTH = 500

# Default log format and level
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_LOG_LEVEL = "ERROR"

# Configure logger (always enabled)
#
# ⚠️ 这里**不再**自己装 handler：``pyromind_sdk`` 父 logger 已经有唯一一个
# 带 trace_id 的 handler（见 base.py / trace.py）。这里再装一个 + 向 root 传播
# ⇒ 同一条日志打印三遍。
logger = logging.getLogger("pyromind_sdk.async")


def accept_encoding() -> str:
    """The ``Accept-Encoding`` value async API requests are sent with.

    ``PYROMIND_ACCEPT_ENCODING`` wins when set (``"br, gzip, deflate"`` for an
    environment whose aiohttp/brotli pair is known good), otherwise
    :data:`DEFAULT_ACCEPT_ENCODING`. Never empty — an empty header is rejected
    by some edges.
    """
    return (os.getenv(ENV_ACCEPT_ENCODING) or "").strip() or DEFAULT_ACCEPT_ENCODING


class PyroMindAsyncAPIError(Exception):
    """Base exception for PyroMind Async API errors"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None, headers: Optional[Dict] = None):
        self.message = append_trace_id(message, headers)
        self.status_code = status_code
        self.response = response
        self.headers = headers
        self.trace_id = extract_trace_id(headers)
        super().__init__(self.message)


class PyroMindAsyncClient:
    """
    Async base client class for PyroMind API

    Handles authentication, async HTTP requests, and error handling.
    All resource-specific async clients inherit from this class.

    Args:
        api_key: Bearer token for API authentication. If not provided, will try to
                read from PYROMIND_API_KEY environment variable. If neither is
                provided, will raise ValueError.
        base_url: Base URL for the API. If not provided, will try to read from
                 PYROMIND_BASE_URL environment variable. If neither is provided,
                 defaults to https://api-portal.pyromind.ai/api/v1
        cluster: Target cluster identifier. Will be sent as X-Cluster header
                on every request. If not provided, will try to read from
                PYROMIND_CLUSTER environment variable. Defaults to "default".
        timeout: Request timeout in seconds (default: 600)
        max_retries: Maximum number of retries for failed requests (default: 3)
        connector_limit: Maximum number of pooled connections (default: 256)
        connector_limit_per_host: Per-host connection cap; defaults to
            ``connector_limit``

    Raises:
        ValueError: If api_key is not provided and PYROMIND_API_KEY environment
                   variable is not set.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cluster: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        connector_limit: int = DEFAULT_CONNECTOR_LIMIT,
        connector_limit_per_host: Optional[int] = None,
    ):
        # Get API key from parameter or environment variable
        if api_key is None:
            api_key = os.getenv(ENV_API_KEY)

        if not api_key:
            raise ValueError(
                f"API key is required. Please provide it either as a parameter "
                f"or set the {ENV_API_KEY} environment variable."
            )

        # Strip whitespace from API key
        api_key = api_key.strip()

        # Get base URL from parameter, environment variable, or use default
        if not base_url:
            base_url = os.getenv(ENV_BASE_URL) or DEFAULT_API_BASE_URL

        if not base_url:
            raise ValueError(
                f"Base URL is required. Please provide it either as a parameter "
                f"or set the {ENV_BASE_URL} environment variable."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')

        # Get cluster from parameter, environment variable, or use default
        if not cluster:
            cluster = os.getenv(ENV_CLUSTER) or DEFAULT_CLUSTER
        self.cluster = cluster

        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.connector_limit = max(1, int(connector_limit))
        self.connector_limit_per_host = (
            self.connector_limit
            if connector_limit_per_host is None
            else max(1, int(connector_limit_per_host))
        )

        # Session will be created lazily inside the active event loop.
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock: Optional[asyncio.Lock] = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _get_session_lock(self) -> asyncio.Lock:
        """Create the session lock lazily so it binds to the active event loop."""
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        return self._session_lock

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._closed:
            raise RuntimeError("client is closed")
        async with self._get_session_lock():
            if self._closed:
                raise RuntimeError("client is closed")
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=self.connector_limit,
                    limit_per_host=self.connector_limit_per_host,
                )
                self._session = aiohttp.ClientSession(
                    timeout=self.timeout,
                    connector=connector,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        # Explicit, so aiohttp's "advertise br when brotli is
                        # importable" default cannot pick the decoder that is
                        # broken here. See DEFAULT_ACCEPT_ENCODING.
                        "Accept-Encoding": accept_encoding(),
                        "X-Cluster": self.cluster,
                    }
                )
        return self._session

    def _extract_data(self, response: Dict[str, Any]) -> Any:
        """
        Extract data from API response

        API responses are wrapped in {success: True, data: {...}} format.
        This method extracts the actual data from the response.

        Args:
            response: API response dictionary

        Returns:
            Extracted data from response
        """
        if isinstance(response, dict):
            if "data" in response:
                return response["data"]
            # If response doesn't have 'data' field, return the whole response
            return response
        return response

    # ========== URL and Request Building ==========

    def _build_url(self, endpoint: str) -> str:
        """Build full URL from endpoint"""
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def _build_request_context(self, method: str, url: str) -> str:
        """Build request context string for error messages"""
        return f"{method.upper()} {url}"

    # ========== Error Data Extraction ==========

    async def _extract_error_data(
        self,
        response: aiohttp.ClientResponse,
    ) -> Dict[str, Any]:
        """Extract error data from response, handling JSON parse failures"""
        try:
            return await response.json()
        except Exception:
            return {"message": await response.text()}

    def _truncate_error_message(self, error_data: Dict[str, Any], max_length: int = 500) -> Dict[str, Any]:
        """Truncate error message to avoid flooding logs"""
        if isinstance(error_data, dict) and isinstance(error_data.get("message"), str):
            msg = error_data["message"]
            if len(msg) > max_length:
                error_data["message"] = msg[:max_length] + "..."
        return error_data

    # ========== Error Message Formatting ==========

    def _format_400_error(self, response: aiohttp.ClientResponse, error_data: Dict[str, Any]) -> str:
        """Format error message for 400 Bad Request"""
        # 只提取关键错误信息，避免与外层 response 字段重复
        err = error_data.get('error', {})
        code = err.get('code', '')
        msg = err.get('message', '')
        if code or msg:
            return f"{code}: {msg}" if code else msg
        return "Bad Request (400)"

    def _format_401_error(self, error_data: Dict[str, Any]) -> str:
        """Format error message for 401 Unauthorized"""
        error_message = (
            "Authentication failed (401). Please check your API key. "
            "The API key may be invalid, expired, or incorrectly formatted."
        )
        if error_data.get("message"):
            error_message += f" Server message: {error_data.get('message')}"
        return error_message

    def _format_404_error(self, error_data: Dict[str, Any]) -> str:
        """Format error message for 404 Not Found"""
        if not isinstance(error_data, dict):
            return "Resource not found (404). The requested resource does not exist."

        if "error" in error_data:
            error_obj = error_data.get("error", {})
            if isinstance(error_obj, dict):
                error_code = error_obj.get("code", "")
                error_msg = error_obj.get("message", "")
                if error_code:
                    return f"{error_code}: {error_msg}"
                if error_msg:
                    return error_msg

        if "detail" in error_data:
            detail = error_data.get("detail")
            if isinstance(detail, dict) and "error" in detail:
                error_obj = detail.get("error", {})
                if isinstance(error_obj, dict):
                    error_code = error_obj.get("code", "")
                    error_msg = error_obj.get("message", "")
                    if error_code:
                        return f"{error_code}: {error_msg}"
                    if error_msg:
                        return error_msg
            elif isinstance(detail, str):
                return detail

        error_message = "Resource not found (404). The requested resource does not exist."
        if error_data.get("message"):
            error_message += f" Details: {error_data.get('message')}"
        return error_message

    def _format_422_error(self, error_data: Dict[str, Any]) -> str:
        """Format error message for 422 Unprocessable Entity"""
        error_message = "Unprocessable Entity (422). The request was well-formed but contains semantic errors."
        if error_data.get("message"):
            error_message += f" Details: {error_data.get('message')}"
        if error_data.get("detail"):
            error_message += f"\nValidation details: {error_data.get('detail')}"
        return error_message

    def _format_generic_error(self, response: aiohttp.ClientResponse, error_data: Dict[str, Any]) -> str:
        """Format error message for other status codes"""
        error_code = ""
        payload_message = ""

        if isinstance(error_data, dict):
            error_obj = error_data.get("error")
            if isinstance(error_obj, dict):
                if isinstance(error_obj.get("message"), str):
                    payload_message = error_obj.get("message", "")
                error_code = (
                    error_obj.get("code")
                    or error_obj.get("error_code")
                    or ""
                )

            detail = error_data.get("detail")
            if not error_code and isinstance(detail, dict):
                nested_error = detail.get("error")
                if isinstance(nested_error, dict):
                    if isinstance(nested_error.get("message"), str):
                        payload_message = nested_error.get("message", "")
                    error_code = (
                        nested_error.get("code")
                        or nested_error.get("error_code")
                        or ""
                    )

        error_message = payload_message or error_data.get("message", "")
        if not error_message:
            error_message = f"API request failed with status {response.status}"

        if error_code:
            error_message = f"{error_code}: {error_message}"

        if error_data.get("detail"):
            error_message += f"\nDetails: {error_data.get('detail')}"

        return error_message

    def _format_error_message(self, response: aiohttp.ClientResponse, error_data: Dict[str, Any]) -> str:
        """Format error message based on status code"""
        status_code = response.status

        if status_code == 400:
            return self._format_400_error(response, error_data)
        elif status_code == 401:
            return self._format_401_error(error_data)
        elif status_code == 404:
            return self._format_404_error(error_data)
        elif status_code == 422:
            return self._format_422_error(error_data)
        else:
            return self._format_generic_error(response, error_data)

    # ========== Error Response Handling ==========

    async def _handle_error_response(self, response: aiohttp.ClientResponse, request_context: str) -> None:
        """Handle HTTP error response and raise appropriate exception"""
        error_data = await self._extract_error_data(response)
        error_data = self._truncate_error_message(error_data)
        error_message = self._format_error_message(response, error_data)

        # 单行错误日志（trace_id 在格式前缀里，不再倒整个 headers）
        resp_headers = dict(response.headers)
        logger.error(
            "[ERROR] %s -> %s: %s",
            request_context,
            response.status,
            error_message,
        )

        raise PyroMindAsyncAPIError(
            message=f"{request_context} failed: {error_message}",
            status_code=response.status,
            response=error_data,
            headers=resp_headers
        )

    # ========== Raw (non-JSON) Requests ==========

    def _trace_headers(self) -> Dict[str, str]:
        """给绕过 ``_request`` 的请求补 trace 头。"""
        return {TRACE_ID_HEADER: ensure_trace_id()}

    @asynccontextmanager
    async def _raw_request(self, session, method: str, url: str, **kwargs):
        """文件读写 / 分块上传这类「非 JSON」请求的薄封装（异步）。

        与同步版同义：带上 trace，进上下文时记「请求」，拿到响应头时记
        「服务端返回」。用 ``async with`` 保持和原来 ``session.request`` 一样的写法。
        """
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault(TRACE_ID_HEADER, ensure_trace_id())
        kwargs["headers"] = headers
        logger.debug("[REQUEST] %s %s", method.upper(), url)
        async with session.request(method, url, **kwargs) as response:
            logger.debug(
                "[RESPONSE] %s %s -> %s", method.upper(), url, response.status
            )
            yield response

    # ========== Main Request Method ==========

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        retry: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """入口：保证整次调用（含「服务端返回」那几行日志）都有 trace_id。

        没有上下文（SDK 单独用 / 后台任务）就地开一条，结束还原 ——
        不能沿用上一次，否则两条不相干的请求会共用一个 trace。
        """
        if current_trace_id():
            return await self._request_inner(
                method,
                endpoint,
                params=params,
                json_data=json_data,
                retry=retry,
                **kwargs,
            )
        token = set_trace_id(new_trace_id())
        try:
            return await self._request_inner(
                method,
                endpoint,
                params=params,
                json_data=json_data,
                retry=retry,
                **kwargs,
            )
        except Exception as exc:
            # 网关层的 5xx（如 istio "no healthy upstream"）**不带 x-trace-id**，
            # 那就把我们自己这条挂上去 —— 上层用 format_exception_message 时
            # 错误文本里也会出现 (trace_id=...)，日志/CLI 两边都能对上。
            if getattr(exc, "trace_id", None) is None:
                try:
                    exc.trace_id = current_trace_id()
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            reset_trace_id(token)

    async def _request_inner(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        retry: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make an async HTTP request to the API

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            json_data: JSON request body
            retry: Whether transient transport failures may be retried. This
                must be False for non-idempotent create-style operations.
            **kwargs: Additional arguments to pass to aiohttp

        Returns:
            Response JSON data as dictionary

        Raises:
            PyroMindAsyncAPIError: If the request fails
        """
        url = self._build_url(endpoint)
        request_context = self._build_request_context(method, url)
        session = await self._get_session()

        # 出站带上 trace_id（middleware 有「来什么用什么」，于是全程同一条）
        trace_id = ensure_trace_id()
        extra_headers = dict(kwargs.pop("headers", None) or {})
        extra_headers.setdefault(TRACE_ID_HEADER, trace_id)
        kwargs["headers"] = extra_headers

        # 单行请求日志
        log_parts = [f"[REQUEST] {method.upper()} {url}"]
        if params:
            log_parts.append(f"params={params}")
        if json_data:
            log_parts.append(f"body={self._mask_sensitive_data(json_data)}")
        log_parts.append(f"trace={trace_id}")
        logger.debug(" | ".join(log_parts))

        last_exception = None
        attempts = max(1, self.max_retries) if retry else 1
        for attempt in range(attempts):
            try:
                async with session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    **kwargs
                ) as response:
                    # Log response
                    logger.debug(f"[RESPONSE] {request_context} - Status: {response.status}")
                    try:
                        if response.content:
                            resp_json = await response.json()
                            safe_resp = self._mask_sensitive_data(resp_json)
                            logger.debug(f"[RESPONSE] body: {safe_resp}")
                    except Exception:
                        logger.debug(f"[RESPONSE] body: <non-JSON content>")

                    # Handle non-2xx responses
                    if not response.ok:
                        if (
                            response.status in RETRY_STATUS_CODES
                            and attempt < attempts - 1
                        ):
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        await self._handle_error_response(response, request_context)

                    # Return JSON response
                    if response.content:
                        return await response.json()
                    return {}

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_exception = e
                # Log exception
                logger.error(f"[ERROR] {request_context} - {type(e).__name__}: {str(e)} (attempt {attempt + 1}/{attempts})")
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise PyroMindAsyncAPIError(
                    message=f"{request_context} request failed: {type(e).__name__}: {str(e)}",
                    status_code=None
                )

        # If we get here, all retries failed
        logger.error(f"[ERROR] {request_context} - All {attempts} attempts failed")
        raise PyroMindAsyncAPIError(
            message=f"{request_context} request failed after {attempts} attempts: {str(last_exception)}",
            status_code=None
        )
    
    def _mask_sensitive_data(self, data: Any, mask: str = "***") -> Any:
        """
        Mask sensitive data in logs
        
        Args:
            data: Data to mask
            mask: Mask string to use
            
        Returns:
            Data with sensitive fields masked
        """
        sensitive_keys = {'password', 'api_key', 'apikey', 'token', 'secret', 'authorization'}
        
        if isinstance(data, dict):
            masked = {}
            for key, value in data.items():
                if key.lower() in sensitive_keys:
                    masked[key] = mask
                elif isinstance(value, (dict, list)):
                    masked[key] = self._mask_sensitive_data(value, mask)
                else:
                    masked[key] = value
            return masked
        elif isinstance(data, list):
            return [self._mask_sensitive_data(item, mask) for item in data]
        return data

    async def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Make an async GET request"""
        return await self._request("GET", endpoint, params=params, **kwargs)

    async def post(
        self,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        *,
        retry: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make an async POST request"""
        return await self._request(
            "POST",
            endpoint,
            json_data=json_data,
            retry=retry,
            **kwargs,
        )

    async def put(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Make an async PUT request"""
        return await self._request("PUT", endpoint, json_data=json_data, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an async DELETE request"""
        return await self._request("DELETE", endpoint, **kwargs)

    async def close(self):
        """Close the session"""
        async with self._get_session_lock():
            self._closed = True
            session = self._session
            self._session = None
        if session and not session.closed:
            await session.close()

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
