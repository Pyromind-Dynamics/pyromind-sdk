"""
Async Base client class for PyroMind API

This module provides the async base client class that handles authentication,
HTTP requests, and error handling for all async API clients.
"""

import os
import logging
import aiohttp
from typing import Optional, Dict, Any


# Constants
DEFAULT_API_BASE_URL = "https://api-portal.pyromind.ai/api/v1"
DEFAULT_CLUSTER = "us-west-2"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
ENV_API_KEY = "PYROMIND_API_KEY"
ENV_BASE_URL = "PYROMIND_BASE_URL"
ENV_CLUSTER = "PYROMIND_CLUSTER"
ENV_LOG_FORMAT = "PYROMIND_LOG_FORMAT"
RETRY_STATUS_CODES = [500, 502, 503, 504]

ERROR_MESSAGE_MAX_LENGTH = 500

# Default log format
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Configure logger (always enabled)
logger = logging.getLogger("pyromind_sdk.async")
logger.setLevel(logging.INFO)

# Configure handler with format from environment variable or default
_log_format = os.getenv(ENV_LOG_FORMAT, DEFAULT_LOG_FORMAT)
_handler = logging.StreamHandler()
_handler.setLevel(logging.INFO)
_handler.setFormatter(logging.Formatter(_log_format))
logger.addHandler(_handler)


class PyroMindAsyncAPIError(Exception):
    """Base exception for PyroMind Async API errors"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None):
        self.message = message
        self.status_code = status_code
        self.response = response
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
                 defaults to https://api.pyromind.ai/api/v1
        cluster: Target cluster identifier. Will be sent as X-Cluster header
                on every request. If not provided, will try to read from
                PYROMIND_CLUSTER environment variable. Defaults to "default".
        timeout: Request timeout in seconds (default: 30)
        max_retries: Maximum number of retries for failed requests (default: 3)

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
        max_retries: int = DEFAULT_MAX_RETRIES
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
        if base_url is None:
            base_url = os.getenv(ENV_BASE_URL, DEFAULT_API_BASE_URL)

        if not base_url:
            raise ValueError(
                f"Base URL is required. Please provide it either as a parameter "
                f"or set the {ENV_BASE_URL} environment variable."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')

        # Get cluster from parameter, environment variable, or use default
        if cluster is None:
            cluster = os.getenv(ENV_CLUSTER, DEFAULT_CLUSTER)
        self.cluster = cluster

        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries

        # Session will be created lazily
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
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

    def _extract_error_data(self, response: aiohttp.ClientResponse) -> Dict[str, Any]:
        """Extract error data from response, handling JSON parse failures"""
        try:
            return response.json()
        except Exception:
            return {"message": response.text}

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
        base_message = (
            "Bad Request (400). The request was invalid or malformed. "
            "This usually means there's an issue with the request parameters, "
            "request body format, or missing required fields."
        )
        
        # response.text is a coroutine in aiohttp, we need to handle this
        # Since we're in sync context, we'll use the error_data instead
        if 'error' in error_data and 'message' in error_data['error']:
            detail = error_data['error']['message']
            return f"{base_message} Detail: {detail}"
        
        return base_message
        suffix = "..." if len(response.text) > ERROR_MESSAGE_MAX_LENGTH else ""
        return f"{base_message}\nResponse: {response_text}{suffix}"

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

        # Log error (single line)
        safe_error_data = self._mask_sensitive_data(error_data)
        logger.error(f"[ERROR] {request_context} - Status: {response.status} | message: {error_message} | response: {safe_error_data}")

        raise PyroMindAsyncAPIError(
            message=f"{request_context} failed: {error_message}",
            status_code=response.status,
            response=error_data
        )

    # ========== Main Request Method ==========

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make an async HTTP request to the API

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            json_data: JSON request body
            **kwargs: Additional arguments to pass to aiohttp

        Returns:
            Response JSON data as dictionary

        Raises:
            PyroMindAsyncAPIError: If the request fails
        """
        url = self._build_url(endpoint)
        request_context = self._build_request_context(method, url)
        session = await self._get_session()

        # Log request
        logger.info(f"[REQUEST] {request_context}")
        if params:
            logger.info(f"[REQUEST] params: {params}")
        if json_data:
            safe_json = self._mask_sensitive_data(json_data)
            logger.info(f"[REQUEST] body: {safe_json}")
        # Log request headers (mask authorization)
        safe_headers = {k: '***' if k.lower() == 'authorization' else v for k, v in session.headers.items()}
        logger.info(f"[REQUEST] headers: {safe_headers}")

        last_exception = None
        for attempt in range(self.max_retries):
            try:
                async with session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    **kwargs
                ) as response:
                    # Log response
                    logger.info(f"[RESPONSE] {request_context} - Status: {response.status}")
                    try:
                        if response.content:
                            resp_json = await response.json()
                            safe_resp = self._mask_sensitive_data(resp_json)
                            logger.info(f"[RESPONSE] body: {safe_resp}")
                    except Exception:
                        logger.info(f"[RESPONSE] body: <non-JSON content>")

                    # Handle non-2xx responses
                    if not response.ok:
                        await self._handle_error_response(response, request_context)

                    # Return JSON response
                    if response.content:
                        return await response.json()
                    return {}

            except aiohttp.ClientError as e:
                last_exception = e
                # Log exception
                logger.error(f"[ERROR] {request_context} - {type(e).__name__}: {str(e)} (attempt {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    import asyncio
                    await asyncio.sleep(1 ** attempt)  # Exponential backoff
                    continue
                raise PyroMindAsyncAPIError(
                    message=f"{request_context} request failed: {type(e).__name__}: {str(e)}",
                    status_code=None
                )

        # If we get here, all retries failed
        logger.error(f"[ERROR] {request_context} - All {self.max_retries} retries failed")
        raise PyroMindAsyncAPIError(
            message=f"{request_context} request failed after {self.max_retries} retries: {str(last_exception)}",
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

    async def post(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Make an async POST request"""
        return await self._request("POST", endpoint, json_data=json_data, **kwargs)

    async def put(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Make an async PUT request"""
        return await self._request("PUT", endpoint, json_data=json_data, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an async DELETE request"""
        return await self._request("DELETE", endpoint, **kwargs)

    async def close(self):
        """Close the session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
