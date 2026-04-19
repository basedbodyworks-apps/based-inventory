"""Tests for atc_audit v0.5: handle-based observation matching + Slack blocks."""

import dataclasses
import json

from based_inventory.crawl.atc import VariantObservation
from based_inventory.crawl.diff import Flag, FlagType
from based_inventory.jobs.atc_audit import (
    _dedupe_flags_by_state_key,
    _flags_for_observation,
    build_atc_blocks,
    compute_expected_products,
)
from based_inventory.sets import SetResolver


def _level(qty, ships=True):
    return {"available": qty, "location": {"id": "L1", "name": "TX", "shipsInventory": ships}}


def _variant(gid, title, qty, policy="DENY"):
    return {
        "id": gid,
        "title": title,
        "sku": None,
        "inventoryQuantity": qty,
        "inventoryPolicy": policy,
        "inventoryItem": {"tracked": True, "inventoryLevels": [_level(qty)]},
    }


def test_atc_blocks_silent_no_mentions():
    flags = [
        Flag(
            flag_type=FlagType.SALES_LEAK,
            product_title="Curl Cream",
            variant_gid="gid1",
            variant_label=None,
            url="https://basedbodyworks.com/products/curl-cream",
            expected_sellable=True,
            observed_text="SOLD OUT",
            state_key="gid1::...::SALES_LEAK",
        ),
        Flag(
            flag_type=FlagType.OVERSELL_RISK,
            product_title="Shower Duo",
            variant_gid="gid2",
            variant_label=None,
            url="https://basedbodyworks.com/products/shower-duo",
            expected_sellable=False,
            observed_text="ADD TO CART",
            state_key="gid2::...::OVERSELL_RISK",
        ),
    ]
    blocks = build_atc_blocks(flags)

    texts = "\n".join(
        b["text"]["text"] for b in blocks if b.get("type") == "section" and "text" in b
    )
    assert "SALES LEAK" in texts
    assert "OVERSELL RISK" in texts
    assert "Curl Cream" in texts
    assert "Shower Duo" in texts
    assert "v0 limitation" in texts  # only on OVERSELL

    footer = blocks[-1]["elements"][0]["text"]
    assert "<@" not in footer
    assert "<!channel>" not in footer


def test_dedupe_flags_by_state_key():
    base = Flag(
        flag_type=FlagType.SALES_LEAK,
        product_title="Shampoo",
        variant_gid="gid1",
        variant_label=None,
        url="https://x/products/shampoo",
        expected_sellable=True,
        observed_text="SOLD OUT",
        state_key="gid1::https://x/products/shampoo::SALES_LEAK",
    )
    duplicate = dataclasses.replace(base, variant_label="Just One")
    other = dataclasses.replace(
        base,
        flag_type=FlagType.OVERSELL_RISK,
        product_title="Conditioner",
        state_key="gid2::https://x/products/conditioner::OVERSELL_RISK",
    )

    result = _dedupe_flags_by_state_key([base, duplicate, other])
    assert len(result) == 2
    assert result[0].variant_label is None  # first occurrence kept
    assert result[1].state_key == other.state_key


def test_compute_expected_products_single_and_set(tmp_path):
    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {"Shower Duo": ["Shampoo", "Conditioner"]}}))
    sr = SetResolver(components_path=components_file)

    products = [
        {
            "id": "gid://shopify/Product/1",
            "title": "Shampoo",
            "handle": "shampoo",
            "totalInventory": 100,
            "variants": [
                _variant("gid://shopify/ProductVariant/11", "Just One", 100),
                _variant("gid://shopify/ProductVariant/12", "Two Pack", 5),
            ],
        },
        {
            "id": "gid://shopify/Product/2",
            "title": "Conditioner",
            "handle": "conditioner",
            "totalInventory": 0,
            "variants": [_variant("gid://shopify/ProductVariant/21", "Just One", 0)],
        },
        {
            "id": "gid://shopify/Product/3",
            "title": "Shower Duo",
            "handle": "shower-duo",
            "totalInventory": 999,
            "variants": [_variant("gid://shopify/ProductVariant/31", "Default Title", 999)],
        },
    ]

    expected = compute_expected_products(products, sr)

    # Indexed by handle
    assert set(expected) == {"shampoo", "conditioner", "shower-duo"}
    # Shampoo default variant (Just One) is sellable
    assert expected["shampoo"].expected.sellable is True
    assert expected["shampoo"].variant_gid == "gid://shopify/ProductVariant/11"
    # Conditioner default variant is OOS
    assert expected["conditioner"].expected.sellable is False
    # Shower Duo: Conditioner component is at 0 singles -> not sellable
    assert expected["shower-duo"].expected.sellable is False


def test_flags_for_observation_matches_by_handle(tmp_path):
    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {}}))
    sr = SetResolver(components_path=components_file)

    products = [
        {
            "id": "gid://shopify/Product/1",
            "title": "Shampoo",
            "handle": "shampoo",
            "totalInventory": 100,
            "variants": [_variant("gid://shopify/ProductVariant/11", "Just One", 100)],
        },
    ]
    expected = compute_expected_products(products, sr)

    # Shopify says shampoo is in stock, but collection card shows Sold Out -> SALES LEAK
    obs = VariantObservation(
        url="https://basedbodyworks.com/collections/all",
        product_handle="shampoo",
        variant_label=None,
        present=True,
        enabled=False,
        text="SOLD OUT",
    )

    flags = _flags_for_observation(obs, expected)
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.SALES_LEAK
    assert flags[0].product_title == "Shampoo"


def test_flags_for_observation_skips_unknown_handle(tmp_path):
    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {}}))
    sr = SetResolver(components_path=components_file)
    expected = compute_expected_products([], sr)

    obs = VariantObservation(
        url="https://basedbodyworks.com/collections/all",
        product_handle="unknown-product",
        variant_label=None,
        present=True,
        enabled=True,
        text="ADD TO CART",
    )

    # Unknown handle (e.g. archived / skipped product) should emit no flags
    assert _flags_for_observation(obs, expected) == []


def test_flags_for_observation_skips_when_handle_is_none(tmp_path):
    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {}}))
    sr = SetResolver(components_path=components_file)
    expected = compute_expected_products([], sr)

    obs = VariantObservation(
        url="https://basedbodyworks.com/pages/about",
        product_handle=None,
        variant_label=None,
        present=True,
        enabled=True,
        text="ADD TO CART",
    )
    # Page-level ATC with no product attribution: can't audit, skip.
    assert _flags_for_observation(obs, expected) == []
