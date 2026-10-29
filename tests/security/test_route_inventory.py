"""Adding a route without listing it (and therefore testing its access) fails the suite (SC-008)."""

from tests.security.route_inventory import INVENTORY
from worklist.web.app import route_table


def test_every_registered_route_is_in_the_inventory():
    missing = [route for route in route_table() if route not in INVENTORY]
    assert not missing, f"Routes without an access rule in route_inventory.py: {missing}"


def test_inventory_has_no_stale_entries():
    registered = set(route_table())
    stale = [route for route in INVENTORY if route not in registered]
    assert not stale, f"Inventory lists routes the app no longer serves: {stale}"
