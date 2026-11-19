"""Providers explicitly included in this Frigate build."""

from .provider import CameraPatch
from .providers.netsdk import NetSdkPatch

PROVIDERS: dict[str, type[CameraPatch]] = {"netsdk": NetSdkPatch}
