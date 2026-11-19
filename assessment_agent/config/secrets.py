"""Secure secret retrieval via Google Cloud Secret Manager.

Enforces zero hardcoded credentials across the repository. In Google Cloud
production runtimes (Agent Engine / Cloud Run), secrets are resolved directly
from Secret Manager by resource name. Local development may explicitly inject
environment variables via `.env` (git-ignored).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from assessment_agent.config.settings import get_settings


class SecretRetrievalError(RuntimeError):
    """Raised when a required secret cannot be resolved securely."""


class SecretManagerClient:
    """Wrapper around Google Cloud Secret Manager with safe local dev fallback."""

    def __init__(self, project_id: Optional[str] = None) -> None:
        self._project_id = project_id or get_settings().gcp_project_id
        self._client = None

    def _get_gcp_client(self):
        if self._client is None:
            from google.cloud import secretmanager  # type: ignore[import-untyped]

            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def access_secret_version(
        self,
        secret_id: str,
        version: str = "latest",
        fallback_env_var: Optional[str] = None,
        required: bool = False,
    ) -> Optional[str]:
        """Fetch a secret from Google Cloud Secret Manager or an explicit local env var.

        Args:
            secret_id: Secret identifier stored in Google Cloud Secret Manager.
            version: Secret version identifier (defaults to "latest").
            fallback_env_var: Optional environment variable for local development.
            required: When True, raises SecretRetrievalError if unresolved.

        Returns:
            The resolved secret string, or None when optional and absent.
        """
        if self._project_id:
            resource_name = (
                f"projects/{self._project_id}/secrets/{secret_id}/versions/{version}"
            )
            try:
                client = self._get_gcp_client()
                response = client.access_secret_version(request={"name": resource_name})
                payload = response.payload.data.decode("utf-8").strip()
                if payload:
                    return payload
            except Exception as exc:  # noqa: BLE001 - graceful fallback when offline/dev
                if not fallback_env_var and required:
                    raise SecretRetrievalError(
                        f"Failed to retrieve secret '{secret_id}' from Secret Manager: {exc}"
                    ) from exc

        if fallback_env_var:
            env_value = os.getenv(fallback_env_var, "").strip()
            if env_value:
                return env_value

        if required:
            raise SecretRetrievalError(
                f"Secret '{secret_id}' is unavailable. Configure Google Cloud Secret Manager "
                f"or set local env var '{fallback_env_var}'."
            )
        return None


@lru_cache(maxsize=16)
def get_secret_value(
    secret_id: str,
    fallback_env_var: Optional[str] = None,
    required: bool = False,
) -> Optional[str]:
    """Cached helper for retrieving secrets via Google Cloud Secret Manager."""
    return SecretManagerClient().access_secret_version(
        secret_id=secret_id,
        fallback_env_var=fallback_env_var,
        required=required,
    )
