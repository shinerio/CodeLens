from enum import StrEnum

from packaging.version import Version


class PluginApiVersion(StrEnum):
    V1 = "1"
    V2 = "2"


class PluginCompatibilityError(ValueError):
    """Raised before activation when a plugin cannot run on this host."""


def ensure_plugin_compatible(
    *,
    plugin_api_version: PluginApiVersion,
    minimum_codelens_version: Version,
    current_codelens_version: Version,
) -> None:
    if plugin_api_version not in {PluginApiVersion.V1, PluginApiVersion.V2}:
        raise PluginCompatibilityError("unsupported plugin API")
    if current_codelens_version < minimum_codelens_version:
        raise PluginCompatibilityError("CodeLens version is below plugin minimum")
