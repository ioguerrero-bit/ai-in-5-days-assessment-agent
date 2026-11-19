"""Configuration module for the AI in 5 Days Multilingual Assessment Agent."""

from assessment_agent.config.secrets import SecretManagerClient, get_secret_value
from assessment_agent.config.settings import AssessmentSettings, get_settings

__all__ = [
    "AssessmentSettings",
    "SecretManagerClient",
    "get_secret_value",
    "get_settings",
]
