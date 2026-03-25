"""
Configuration Module - Mellow-Link

This module provides configuration management using:
- Environment variables
- YAML/JSON config files
- Runtime settings
"""

from .settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
