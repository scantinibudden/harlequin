from typing import Any

import pytest

from harlequin.catalog import Catalog, CatalogItem
from harlequin.exception import HarlequinConfigError
from harlequin.profiles import (
    ProfileCatalogItem,
    build_adapter_for_profile,
    wrap_catalog_with_profiles,
)


class FakeAdapter:
    connection_id = None

    def __init__(self, conn_str: tuple = (), **options: Any) -> None:
        self.conn_str = conn_str
        self.options = options


@pytest.fixture
def inner_catalog() -> Catalog:
    return Catalog(
        items=[
            CatalogItem(
                qualified_identifier='"mydb"',
                query_name='"mydb"',
                label="mydb",
                type_label="db",
            )
        ]
    )


def test_wrap_active_profile(inner_catalog: Catalog) -> None:
    wrapped = wrap_catalog_with_profiles(
        catalog=inner_catalog,
        profile_names=["local", "staging"],
        active_profile_name="staging",
    )
    assert len(wrapped.items) == 2
    [local, staging] = wrapped.items
    assert isinstance(local, ProfileCatalogItem)
    assert isinstance(staging, ProfileCatalogItem)
    assert not local.is_active
    assert local.children == []
    assert local.profile_name == "local"
    assert staging.is_active
    assert staging.children == inner_catalog.items
    assert staging.profile_name == "staging"
    # profile items must never lazy-load children
    assert local.loaded and staging.loaded


def test_wrap_disconnected(inner_catalog: Catalog) -> None:
    wrapped = wrap_catalog_with_profiles(
        catalog=inner_catalog,
        profile_names=["local", "staging"],
        active_profile_name=None,
    )
    assert len(wrapped.items) == 2
    assert all(
        isinstance(item, ProfileCatalogItem) and not item.is_active
        for item in wrapped.items
    )
    assert all(item.children == [] for item in wrapped.items)


def test_wrap_adhoc_session(inner_catalog: Catalog) -> None:
    wrapped = wrap_catalog_with_profiles(
        catalog=inner_catalog,
        profile_names=["local"],
        active_profile_name=None,
        active_label="(session)",
    )
    assert len(wrapped.items) == 2
    [local, session] = wrapped.items
    assert isinstance(local, ProfileCatalogItem)
    assert not local.is_active
    assert isinstance(session, ProfileCatalogItem)
    assert session.is_active
    assert session.label == "(session)"
    assert session.profile_name == ""
    assert session.children == inner_catalog.items


def test_build_adapter_strips_session_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "adapter": "fake",
        "conn_str": "foo://bar",
        "theme": "zenburn",
        "limit": 1000,
        "keymap_name": ["vscode"],
        "my_adapter_opt": "x",
    }
    monkeypatch.setattr(
        "harlequin.profiles.get_config_for_profile", lambda **_: (profile, [])
    )
    monkeypatch.setattr(
        "harlequin.profiles.load_adapter_plugins", lambda: {"fake": FakeAdapter}
    )
    adapter, connection_hash = build_adapter_for_profile(profile_name="p1")
    assert isinstance(adapter, FakeAdapter)
    assert adapter.conn_str == ("foo://bar",)
    assert adapter.options == {"my_adapter_opt": "x"}
    assert isinstance(connection_hash, str) and connection_hash


def test_build_adapter_missing_adapter_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "harlequin.profiles.get_config_for_profile",
        lambda **_: ({"adapter": "not-installed"}, []),
    )
    monkeypatch.setattr("harlequin.profiles.load_adapter_plugins", lambda: {})
    with pytest.raises(HarlequinConfigError):
        build_adapter_for_profile(profile_name="p1")


def test_build_adapter_missing_profile_raises() -> None:
    with pytest.raises(HarlequinConfigError):
        build_adapter_for_profile(profile_name="a-profile-that-does-not-exist-xyz")
