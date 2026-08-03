from __future__ import annotations

from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components import ErrorModal
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.exception import HarlequinConnectionError
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
        if profile_name == "bad":
            raise HarlequinConnectionError("this profile is broken")
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
        labels = sorted(
            str(child.data.label)
            for child in tree.root.children
            if child.data is not None
        )
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


@pytest.mark.asyncio
async def test_failed_switch_keeps_old_connection(
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
        old_connection = app.connection

        app.switch_profile("bad")
        await wait_for_workers(app)
        await pilot.pause()
        # the failed switch must not disconnect the session
        assert app.connection is old_connection
        assert app.profile_name == "p1"
        assert app.connection_hash == "hash-p1"
        assert isinstance(app.screen, ErrorModal)
        await pilot.press("enter")  # dismiss the modal
        assert not app._switching_profiles


@pytest.mark.asyncio
async def test_stale_catalog_error_suppressed_while_switching(
    disconnected_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await pilot.pause()

        # while a switch is in progress, database catalog errors from the
        # old connection are suppressed
        app._switching_profiles = True
        app.post_message(
            HarlequinTree.CatalogError(
                catalog_type="database",
                error=Exception("the pool 'pool-1' is already closed"),
            )
        )
        await pilot.pause()
        assert not isinstance(app.screen, ErrorModal)

        # outside of a switch, the same error is shown
        app._switching_profiles = False
        app.post_message(
            HarlequinTree.CatalogError(
                catalog_type="database",
                error=Exception("some real catalog problem"),
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, ErrorModal)


@pytest.mark.asyncio
async def test_interrupt_load_discards_queue(
    disconnected_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = disconnected_app
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        await pilot.pause()
        tree = app.data_catalog.database_tree
        # stop the background loader so it can't consume the queue mid-test
        app.workers.cancel_node(tree)
        await pilot.pause()
        tree._load_queue.put_nowait((0, "x", 0, None))  # type: ignore[arg-type]
        assert tree._load_queue.qsize() == 1
        tree.interrupt_load()
        assert tree._load_queue.qsize() == 0
