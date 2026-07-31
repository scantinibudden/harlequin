from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Collection, Sequence

from harlequin.catalog import Catalog, CatalogItem, Interaction, InteractiveCatalogItem
from harlequin.catalog_cache import get_connection_hash
from harlequin.config import (
    DEFAULT_ADAPTER,
    TUI_ONLY_KEYS,
    load_profile,
    parse_profile_options,
)
from harlequin.plugins import load_adapter

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter
    from harlequin.driver import HarlequinDriver

IDE_PROFILE_KEYS = frozenset(
    {
        *TUI_ONLY_KEYS,
        "adapter",
        "conn_str",
        "limit",
        "output",
        "read_only",
        "profile",
        "config_path",
        "config",
        "keys",
    }
)
"""Profile keys Harlequin reads for itself; every other key is the adapter's."""

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
    profile_name: str,
    config_path: Path | None = None,
    command_options: Collection[str] | None = None,
) -> tuple["HarlequinAdapter", str]:
    """
    Instantiates (but does not connect) the adapter the named profile defines,
    parsing its options the way the CLI does.

    `command_options` names every key the IDE reads for itself, so that the
    rest can be checked against what the adapter declares; the CLI passes its
    own, and a caller without one gets the keys Harlequin and hsql are known
    to read.

    Returns the adapter instance and its connection hash (for the catalog and
    history caches).

    Raises: HarlequinConfigError if the profile does not exist, names an
    adapter that is not installed, or sets an option the adapter does not have.
    """
    profile = load_profile(config_path, profile_name)
    adapter_name = str(profile.get("adapter") or DEFAULT_ADAPTER)
    adapter_cls = load_adapter(adapter_name)
    if command_options is None:
        command_options = IDE_PROFILE_KEYS | _hsql_profile_keys()
    options: dict[str, Any] = dict(
        parse_profile_options(
            profile,
            adapter=adapter_name,
            adapter_options=adapter_cls.ADAPTER_OPTIONS,
            command_options=command_options,
        )
    )
    raw_conn_str = options.pop("conn_str", tuple())
    conn_str: tuple[str, ...] = (
        (raw_conn_str,) if isinstance(raw_conn_str, str) else tuple(raw_conn_str)
    )
    read_only = bool(options.pop("read_only", False))
    for key in command_options:
        options.pop(key, None)
    adapter = adapter_cls(conn_str=conn_str, read_only=read_only, **options)
    connection_hash = (
        adapter.connection_id
        if adapter.connection_id is not None
        else get_connection_hash(conn_str, options)
    )
    return adapter, connection_hash


def _hsql_profile_keys() -> frozenset[str]:
    # the CLI imports the app, which imports this module
    from harlequin.cli import hsql_profile_keys

    return hsql_profile_keys()


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
