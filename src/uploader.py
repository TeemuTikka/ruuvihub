"""Cloud uploader for RuuviHub.

Uploads batches to an HTTP endpoint (POST) or Azure Blob container (PUT).
Both use ``run_in_executor`` so the asyncio event loop stays responsive.
If no HTTP endpoint is configured yet, it falls back to logging
the JSON payload at INFO level and reports success so the pending queue
can still be exercised during local setup.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from functools import partial
from typing import Optional
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import uuid4

import requests

from settings import Settings, SettingsStore, UPLOAD_DESTINATIONS

logger = logging.getLogger(__name__)


def validate_azure_blob_container_url(url: str) -> None:
    """Accept a container SAS URL, without putting its secret in errors."""
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        valid = (
            parts.scheme == "https" and parts.hostname
            and parts.hostname.endswith((".blob.core.windows.net", ".blob.core.usgovcloudapi.net", ".blob.core.chinacloudapi.cn"))
            and not parts.username and not parts.password and not parts.fragment
            and len(parts.path.strip("/").split("/")) == 1
            and bool(parts.path.strip("/"))
            and bool(query.get("sig")) and query.get("sr") == ["c"]
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter an HTTPS Azure Blob container SAS URL (container scope), including its SAS token.")


class Uploader:
    """Upload readings to a configured cloud endpoint."""

    def __init__(self, settings_store: SettingsStore) -> None:
        self._settings_store = settings_store

    async def upload(self, records: list[dict]) -> bool:
        """Upload records to cloud or log them when no endpoint is configured."""
        if not records:
            logger.debug("Nothing to upload")
            return True

        settings = await self._settings_store.get()
        if settings.cloud_upload_destination == "azure_blob":
            try:
                response = await self._put_blob(settings.azure_blob_container_url.strip(), records)
                response.raise_for_status()
            except (requests.RequestException, ValueError) as exc:
                logger.error("Azure Blob upload failed: %s", self._format_error_message(exc))
                return False
            logger.info("Azure Blob upload successful: %s (%d record(s))", response.status_code, len(records))
            return True
        endpoint = settings.cloud_endpoint_url.strip()
        if not endpoint:
            if settings.cloud_upload_enabled:
                logger.error(
                    "Cloud upload is enabled but no endpoint is configured – "
                    "refusing to report success for %d pending record(s)",
                    len(records),
                )
                return False
            payload = json.dumps(records, indent=2, default=str)
            logger.info(
                "UPLOAD STUB – would POST %d record(s):\n%s", len(records), payload
            )
            return True

        try:
            response = await self._post(endpoint, records, self._build_headers(settings))
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Upload failed: %s", self._format_error_message(exc))
            return False

        logger.info(
            "Upload successful: %s (%d record(s))", response.status_code, len(records)
        )
        return True

    async def test_upload(
        self,
        endpoint_url: Optional[str] = None,
        auth_header_name: Optional[str] = None,
        auth_header_value: Optional[str] = None,
        destination: Optional[str] = None,
        azure_blob_container_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Send a synthetic test payload to validate endpoint connectivity.

        Accepts optional transient overrides so the Config page's "Test
        connection" button can validate candidate values *before* they are
        saved, without ever persisting an unverified (possibly broken)
        endpoint/credential to disk -- if cloud upload were already
        enabled, persisting first would let the background upload loop
        immediately send real pending readings to an unverified endpoint,
        and a failed test would silently clobber a previously working
        configuration.
        """
        settings = await self._settings_store.get()
        selected = destination if destination is not None else settings.cloud_upload_destination
        if selected not in UPLOAD_DESTINATIONS:
            return False, "Select a supported upload destination."
        if selected == "azure_blob":
            container = azure_blob_container_url if azure_blob_container_url is not None else settings.azure_blob_container_url
            payload = [{"mac": "TEST", "timestamp": datetime.now(timezone.utc).isoformat(), "test": True}]
            try:
                response = await self._put_blob(container.strip(), payload, test=True)
                response.raise_for_status()
            except (requests.RequestException, ValueError) as exc:
                return False, self._format_error_message(exc)
            return True, f"Success ({response.status_code}) — test JSON blob created in the container."
        endpoint = (endpoint_url if endpoint_url is not None else settings.cloud_endpoint_url).strip()
        if not endpoint:
            return False, "No cloud endpoint configured"

        header_name = (
            auth_header_name if auth_header_name is not None else settings.cloud_auth_header_name
        ).strip()
        header_value = (
            auth_header_value if auth_header_value is not None else settings.cloud_auth_header_value
        )
        headers = {header_name: header_value} if header_name else {}

        record = [
            {
                "mac": "TEST",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "test": True,
            }
        ]
        try:
            response = await self._post(endpoint, record, headers)
            response.raise_for_status()
        except requests.RequestException as exc:
            return False, self._format_error_message(exc)

        return True, f"Success ({response.status_code})"

    async def _post(
        self, url: str, payload: list[dict], headers: dict[str, str]
    ) -> requests.Response:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(requests.post, url, json=payload, headers=headers, timeout=10),
        )

    async def _put_blob(self, container: str, payload: list[dict], test: bool = False) -> requests.Response:
        validate_azure_blob_container_url(container)
        now = datetime.now(timezone.utc)
        name = f"ruuvihub-{'test-' if test else ''}{now:%Y%m%dT%H%M%S%fZ}-{uuid4().hex}.json"
        parts = urlsplit(container)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/" + name, parts.query, ""))
        headers = {
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2026-02-06",
            "x-ms-date": format_datetime(now, usegmt=True),
            "Content-Type": "application/json",
            "If-None-Match": "*",
        }
        response = await asyncio.get_running_loop().run_in_executor(
            None, partial(requests.put, url, json=payload, headers=headers, timeout=30, allow_redirects=False),
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise requests.RequestException("Azure did not confirm blob creation.", response=response)
        return response

    @staticmethod
    def _build_headers(settings: Settings) -> dict[str, str]:
        header_name = settings.cloud_auth_header_name.strip()
        if not header_name:
            return {}
        return {header_name: settings.cloud_auth_header_value}

    @staticmethod
    def _format_error_message(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if response is not None:
            code = response.headers.get("x-ms-error-code", "")
            message = f"HTTP {response.status_code}" + (f" ({code})" if code else "")
            if code == "AuthorizationPermissionMismatch":
                message += ". Check SAS permissions and the SAS creator's Azure data access."
            return message[:200]
        message = str(exc).strip() or exc.__class__.__name__
        # requests exceptions include request URLs. Never expose SAS tokens
        # or URL credentials in either the UI test result or persisted logs.
        message = re.sub(r"https?://[^\s]+", "[upload URL]", message)
        # urllib3 sometimes reports only the relative path/query rather
        # than an absolute URL in connection and timeout errors.
        message = re.sub(r"(?i)(\bsig=)[^&\s'\"()]+", r"\1[redacted]", message)
        return message[:200]
