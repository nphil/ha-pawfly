"""Leaving a proxy out of one connection attempt (``ble_affinity`` ``excluded_getter``).

The habluetooth selector is faked just far enough to keep its two observable properties: it walks the
paths the manager lists, best first, and it reserves a connection slot only for the path it returns.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from bleak.exc import BleakError

from custom_components.pawfly.ble_affinity import make_affinity_client_class

ADDRESS = "AA:BB:CC:11:22:33"


class FakeScanner:
    def __init__(self, name: str, *, free_slots: int = 3, failures: int = 0) -> None:
        self.name = name
        self.adapter = name
        self.source = f"source-{name}"
        self.free_slots = free_slots
        self.failures = failures
        self.connector = SimpleNamespace(client=object(), can_connect=lambda: self.free_slots > 0)

    def connection_failures(self, _address: str) -> int:
        return self.failures


class FakeManager:
    """The manager's side: the paths per address, and the slots reserved so far."""

    def __init__(self, scanners: list[FakeScanner]) -> None:
        self.scanners = scanners
        self.reserved: list[str] = []
        self.listed = 0

    def async_scanner_devices_by_address(self, address: str, connectable: bool) -> list[Any]:
        self.listed += 1
        assert connectable
        return [
            SimpleNamespace(
                scanner=scanner,
                ble_device=SimpleNamespace(address=address, scanner=scanner),
                advertisement=SimpleNamespace(rssi=-40 - 10 * index),
            )
            for index, scanner in enumerate(self.scanners)  # best signal first
        ]

    def async_allocate_connection_slot(self, ble_device: Any) -> bool:
        scanner = ble_device.scanner
        if scanner.free_slots <= 0:
            return False
        scanner.free_slots -= 1
        self.reserved.append(scanner.name)
        return True


class FakeBase:
    """The habluetooth wrapper's two selection methods, as the affinity class relies on them."""

    def __init__(self) -> None:
        self._HaBleakClientWrapper__address = ADDRESS

    def _async_get_backend_for_ble_device(self, manager: FakeManager, scanner: FakeScanner, ble_device: Any) -> Any:
        if not manager.async_allocate_connection_slot(ble_device):
            return None
        return SimpleNamespace(scanner=scanner, device=ble_device)

    def _async_get_best_available_backend_and_device(self, manager: FakeManager) -> Any:
        for device in manager.async_scanner_devices_by_address(ADDRESS, True):
            if backend := self._async_get_backend_for_ble_device(manager, device.scanner, device.ble_device):
                return backend
        raise BleakError("No backend with an available connection slot")


def select(manager: FakeManager, *, preferred: str | None = None, excluded: str | None = None):
    choices: list[tuple[str, bool]] = []
    client_class = make_affinity_client_class(
        FakeBase,
        lambda: preferred,
        on_choice=lambda name, used: choices.append((name, used)),
        excluded_getter=lambda: excluded,
    )
    backend = client_class()._async_get_best_available_backend_and_device(manager)
    return backend.scanner.name, choices


def test_an_excluded_proxy_is_not_chosen_and_no_slot_is_reserved_on_it():
    manager = FakeManager([FakeScanner("kitchen"), FakeScanner("hall")])  # kitchen has the best signal
    chosen, choices = select(manager, excluded="kitchen")
    assert chosen == "hall"
    assert manager.reserved == ["hall"]  # filtered before habluetooth reserved anything
    assert choices == [("hall", False)]  # the real fallback is what gets reported


def test_without_an_exclusion_the_best_path_is_still_chosen():
    manager = FakeManager([FakeScanner("kitchen"), FakeScanner("hall")])
    assert select(manager)[0] == "kitchen"
    assert manager.reserved == ["kitchen"]


@pytest.mark.parametrize("name", ["kitchen", "source-kitchen"])
def test_the_exclusion_matches_by_node_name_or_source(name):
    manager = FakeManager([FakeScanner("kitchen"), FakeScanner("hall")])
    assert select(manager, excluded=name)[0] == "hall"


def test_the_only_path_is_used_even_when_it_is_the_excluded_one():
    manager = FakeManager([FakeScanner("kitchen")])
    chosen, choices = select(manager, excluded="kitchen")
    assert chosen == "kitchen" and manager.reserved == ["kitchen"]
    assert choices == [("kitchen", False)]


def test_an_exclusion_that_names_a_proxy_without_a_path_changes_nothing():
    manager = FakeManager([FakeScanner("kitchen"), FakeScanner("hall")])
    assert select(manager, excluded="garage")[0] == "kitchen"


def test_when_the_other_paths_have_no_free_slot_the_excluded_one_is_used_and_nothing_leaks():
    manager = FakeManager([FakeScanner("kitchen"), FakeScanner("hall", free_slots=0)])
    chosen, _choices = select(manager, excluded="kitchen")
    assert chosen == "kitchen"
    assert manager.reserved == ["kitchen"]  # the failed filtered pass reserved nothing


def test_an_excluded_preferred_proxy_is_skipped_and_the_fallback_is_reported_as_not_preferred():
    manager = FakeManager([FakeScanner("hall"), FakeScanner("kitchen")])
    chosen, choices = select(manager, preferred="kitchen", excluded="kitchen")
    assert chosen == "hall" and manager.reserved == ["hall"]
    assert choices == [("hall", False)]


def test_the_preferred_proxy_is_used_again_when_nothing_is_excluded():
    manager = FakeManager([FakeScanner("hall"), FakeScanner("kitchen")])
    chosen, choices = select(manager, preferred="kitchen")
    assert chosen == "kitchen" and choices == [("kitchen", True)]
