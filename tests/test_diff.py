"""Tests for expected-vs-observed ATC state diffing."""

from based_inventory.crawl.atc import VariantObservation
from based_inventory.crawl.diff import ExpectedState, Flag, FlagType, generate_flags  # noqa: F401


def _obs(url, variant=None, present=True, enabled=True, text="Add to cart"):
    return VariantObservation(
        url=url, variant_label=variant, present=present, enabled=enabled, text=text
    )


def test_match_produces_no_flag():
    expected = ExpectedState(sellable=True, inventory_policy="DENY")
    obs = _obs("https://x/products/a")
    flags = generate_flags(expected, obs, variant_gid="v1", product_title="Shampoo")
    assert flags == []


def test_sales_leak_when_sellable_but_atc_disabled():
    expected = ExpectedState(sellable=True, inventory_policy="DENY")
    obs = _obs("https://x/products/a", enabled=False, text="Sold out")
    flags = generate_flags(expected, obs, variant_gid="v1", product_title="Shampoo")
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.SALES_LEAK


def test_oversell_risk_when_oos_but_atc_enabled():
    expected = ExpectedState(sellable=False, inventory_policy="DENY")
    obs = _obs("https://x/products/a", enabled=True, text="Add to cart")
    flags = generate_flags(expected, obs, variant_gid="v1", product_title="Shampoo")
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.OVERSELL_RISK


def test_no_buy_button_when_missing():
    expected = ExpectedState(sellable=True, inventory_policy="DENY")
    obs = _obs("https://x/pages/x", present=False, enabled=False, text="")
    flags = generate_flags(expected, obs, variant_gid="v1", product_title="Shampoo")
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.NO_BUY_BUTTON


def test_inventory_policy_continue_suppresses_oversell_risk():
    """inventoryPolicy=CONTINUE means backorder allowed; ATC enabled when OOS is intentional."""
    expected = ExpectedState(sellable=False, inventory_policy="CONTINUE")
    obs = _obs("https://x/products/a", enabled=True, text="Add to cart")
    flags = generate_flags(expected, obs, variant_gid="v1", product_title="Shampoo")
    assert flags == []


def test_flag_state_key_uniqueness():
    expected = ExpectedState(sellable=True, inventory_policy="DENY")
    obs = _obs("https://x/products/a", enabled=False, text="Sold out")
    flag = generate_flags(
        expected, obs, variant_gid="gid://shopify/ProductVariant/11", product_title="S"
    )[0]
    assert flag.state_key == "gid://shopify/ProductVariant/11::https://x/products/a::SALES_LEAK"
