"""Providers explicitly included in this Frigate build."""

from .provider import CameraPatch

PROVIDERS: dict[str, type[CameraPatch]] = {}
