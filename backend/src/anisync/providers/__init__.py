"""List providers. Importing this package registers every provider module below.

Adding a provider = one new module here that defines a client and registers a `Provider`, plus an import line
below. Nothing else in the app names a specific provider.
"""

from anisync.providers.registry import UnknownProvider, all_providers, get_provider, is_registered

from . import anilist, kitsu, mal  # noqa: F401, E402  (registers the providers)

__all__ = ["UnknownProvider", "all_providers", "get_provider", "is_registered"]
