from __future__ import annotations

from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.profiles import ProfileCatalogItem


@pytest.fixture
def disconnected_app() -> Harlequin:
    return Harlequin(adapter=None, available_profiles=["p1", "p2"])


@pytest.fixture
def patch_build_adapter(
    monkeypatch: pytest.MonkeyPatch, duckdb_adapter: type[HarlequinAdapter]
) -> None:
    def fake_build(
        profile_name: str, config_path: object = None
    ) -> tuple[HarlequinAdapter, str]:
        return duckdb_adapter([":memory:"], no_init=True), f"hash-{profile_name}"

    monkeypatch.setattr("harlequin.app.build_adapter_for_profile", fake_build)


@pytest.mark.asyncio
async def test_starts_disconnected_with_profile_list(
    disconnected_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await pilot.pause()
        assert app.connection is None
        tree = app.data_catalog.database_tree
        while not tree.root.children:
            await pilot.pause()
        assert len(tree.root.children) == 2
        for child in tree.root.children:
            assert isinstance(child.data, ProfileCatalogItem)
            assert not child.data.is_active
            assert not child.allow_expand
        labels = sorted(str(child.data.label) for child in tree.root.children)
        assert labels == ["p1", "p2"]


@pytest.mark.asyncio
async def test_switch_profile_connects(
    disconnected_app: Harlequin,
    patch_build_adapter: None,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await pilot.pause()
        assert app.connection is None

        app.switch_profile("p1")
        await wait_for_workers(app)
        await pilot.pause()
        assert app.connection is not None
        assert app.profile_name == "p1"
        assert app.connection_hash == "hash-p1"

        tree = app.data_catalog.database_tree
        while not any(
            isinstance(child.data, ProfileCatalogItem) and child.data.is_active
            for child in tree.root.children
        ):
            await pilot.pause()
        active = [
            child
            for child in tree.root.children
            if isinstance(child.data, ProfileCatalogItem) and child.data.is_active
        ]
        assert len(active) == 1
        assert active[0].data is not None
        assert active[0].data.profile_name == "p1"
        # the active node holds the real catalog as children
        assert active[0].data.children


@pytest.mark.asyncio
async def test_switch_between_profiles(
    disconnected_app: Harlequin,
    patch_build_adapter: None,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        app.switch_profile("p1")
        await wait_for_workers(app)
        await pilot.pause()
        assert app.profile_name == "p1"
        first_connection = app.connection

        app.switch_profile("p2")
        await wait_for_workers(app)
        await pilot.pause()
        assert app.profile_name == "p2"
        assert app.connection_hash == "hash-p2"
        assert app.connection is not None
        assert app.connection is not first_connection

        # switching to the already-active profile is a no-op
        second_connection = app.connection
        app.switch_profile("p2")
        await wait_for_workers(app)
        await pilot.pause()
        assert app.connection is second_connection


@pytest.mark.asyncio
async def test_submitting_profile_node_connects(
    disconnected_app: Harlequin,
    patch_build_adapter: None,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await pilot.pause()
        tree = app.data_catalog.database_tree
        while not tree.root.children:
            await pilot.pause()
        node = next(
            child
            for child in tree.root.children
            if isinstance(child.data, ProfileCatalogItem)
            and child.data.profile_name == "p2"
        )
        app.post_message(HarlequinTree.NodeSubmitted(node=node))
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        assert app.connection is not None
        assert app.profile_name == "p2"
