from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Sequence

from harlequin.catalog import Catalog, CatalogItem, Interaction, InteractiveCatalogItem
from harlequin.catalog_cache import get_connection_hash
from harlequin.config import get_config_for_profile
from harlequin.exception import HarlequinConfigError
from harlequin.plugins import load_adapter_plugins

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter
    from harlequin.driver import HarlequinDriver

# profile keys that configure the Harlequin session, not the adapter connection;
# these must be stripped before passing the remaining options to the adapter,
# and are ignored when switching profiles at runtime.
SESSION_ONLY_KEYS = frozenset(
    {
        "theme",
        "limit",
        "keymap_name",
        "show_files",
        "show_s3",
        "locale",
        "no_download_tzdata",
    }
)

PROFILE_QUALIFIER_PREFIX = "__harlequin_profile__"


def connect_to_profile(item: "ProfileCatalogItem", driver: "HarlequinDriver") -> None:
    if item.profile_name:
        driver.connect_to_profile(item.profile_name)


@dataclass
class ProfileCatalogItem(InteractiveCatalogItem):
    """
    A synthetic top-level Data Catalog node representing a connection profile
    from the user's config file. Not a database object: submitting an inactive
    profile node connects to that profile instead of inserting text.
    """

    INTERACTIONS: ClassVar[Sequence[tuple[str, Interaction]]] = [
        ("Connect", connect_to_profile),
    ]
    loaded: bool = True
    profile_name: str = ""
    is_active: bool = False


def build_adapter_for_profile(
    profile_name: str, config_path: Path | None = None
) -> tuple["HarlequinAdapter", str]:
    """
    Instantiates (but does not connect) the adapter defined by the named
    profile in the user's config files, mirroring the CLI's option handling.

    Returns the adapter instance and its connection hash (for the catalog and
    history caches).

    Raises: HarlequinConfigError if the profile does not exist or the adapter
    can't be loaded or instantiated.
    """
    profile, _ = get_config_for_profile(
        config_path=config_path, profile_name=profile_name
    )
    options: dict[str, Any] = {
        k: v for k, v in profile.items() if k not in SESSION_ONLY_KEYS
    }
    raw_conn_str = options.pop("conn_str", tuple())
    conn_str: tuple[str, ...] = (
        (raw_conn_str,) if isinstance(raw_conn_str, str) else tuple(raw_conn_str)
    )
    adapter_name = str(options.pop("adapter", "duckdb"))
    adapters = load_adapter_plugins()
    adapter_cls = adapters.get(adapter_name)
    if adapter_cls is None:
        raise HarlequinConfigError(
            f"The profile named {profile_name} uses the adapter {adapter_name}, "
            "but no installed adapter plug-in has that name.",
            title="Harlequin could not load your profile.",
        )
    adapter = adapter_cls(conn_str=conn_str, **options)
    connection_hash = (
        adapter.connection_id
        if adapter.connection_id is not None
        else get_connection_hash(conn_str, options)
    )
    return adapter, connection_hash


def wrap_catalog_with_profiles(
    catalog: Catalog,
    profile_names: Sequence[str],
    active_profile_name: str | None,
    active_label: str | None = None,
) -> Catalog:
    """
    Returns a new Catalog whose top-level items are one ProfileCatalogItem per
    configured profile. The active profile's node holds the real catalog's
    items as children; inactive profiles are leaf nodes that connect when
    submitted.

    If the session is connected to something that isn't a configured profile
    (e.g., an ad-hoc CONN_STR from the CLI), pass active_profile_name=None and
    an active_label to show it as an extra, active node.
    """
    items: list[CatalogItem] = []
    seen_active = False
    for name in profile_names:
        is_active = name == active_profile_name
        seen_active = seen_active or is_active
        items.append(
            ProfileCatalogItem(
                qualified_identifier=f"{PROFILE_QUALIFIER_PREFIX}.{name}",
                query_name="",
                label=name,
                type_label="⏻ connected" if is_active else "profile",
                children=list(catalog.items) if is_active else [],
                profile_name=name,
                is_active=is_active,
            )
        )
    if not seen_active and active_label is not None:
        items.append(
            ProfileCatalogItem(
                qualified_identifier=f"{PROFILE_QUALIFIER_PREFIX}.{active_label}",
                query_name="",
                label=active_label,
                type_label="⏻ connected",
                children=list(catalog.items),
                profile_name="",
                is_active=True,
            )
        )
    return Catalog(items=items)
