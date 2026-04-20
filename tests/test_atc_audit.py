"""Tests for atc_audit v0.6: variant-aware ExpectedProduct + observation matching."""

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
            product_title="Body Care Set",
            variant_gid="gid2",
            variant_label="Santal + Santal",
            url="https://basedbodyworks.com/products/body-care-set",
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
    assert "Body Care Set" in texts
    assert "Santal + Santal" in texts  # variant label rendered
    assert "v0 limitation" in texts

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
    assert result[0].variant_label is None
    assert result[1].state_key == other.state_key


def _sr(tmp_path):
    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {}}))
    return SetResolver(components_path=components_file)


def test_compute_expected_products_indexes_every_variant(tmp_path):
    """Multi-variant scent set: every Shopify variant becomes an ExpectedVariant."""
    sr = _sr(tmp_path)
    products = [
        {
            "id": "gid://shopify/Product/1",
            "title": "Body Care Set",
            "handle": "body-care-set",
            "totalInventory": 12672,
            "variants": [
                _variant(
                    "gid://shopify/ProductVariant/11",
                    "Santal Sandalwood + Santal Sandalwood",
                    10401,
                ),
                _variant("gid://shopify/ProductVariant/12", "Guava Nectar + Guava Nectar", 0),
                _variant(
                    "gid://shopify/ProductVariant/13", "Santal Sandalwood + Guava Nectar", 2271
                ),
            ],
        },
    ]

    expected = compute_expected_products(products, sr)
    product = expected["body-care-set"]

    assert len(product.variants) == 3
    labels = product.variant_labels()
    assert labels == [
        "Santal Sandalwood + Santal Sandalwood",
        "Guava Nectar + Guava Nectar",
        "Santal Sandalwood + Guava Nectar",
    ]
    by_label = {v.variant_label: v for v in product.variants}
    assert by_label["Santal Sandalwood + Santal Sandalwood"].expected.sellable is True
    assert by_label["Guava Nectar + Guava Nectar"].expected.sellable is False
    assert by_label["Santal Sandalwood + Guava Nectar"].expected.sellable is True


def test_flags_for_observation_matches_variant_label_exactly(tmp_path):
    """SALES LEAK only fires when the matched variant says sellable and the
    observed text says sold out. Guava variant (OOS) showing SOLD OUT is
    correct behavior and must produce no flag."""
    sr = _sr(tmp_path)
    products = [
        {
            "id": "gid://shopify/Product/1",
            "title": "Body Care Set",
            "handle": "body-care-set",
            "totalInventory": 12672,
            "variants": [
                _variant(
                    "gid://shopify/ProductVariant/11",
                    "Santal Sandalwood + Santal Sandalwood",
                    10401,
                ),
                _variant("gid://shopify/ProductVariant/12", "Guava Nectar + Guava Nectar", 0),
            ],
        },
    ]
    expected = compute_expected_products(products, sr)

    # Guava variant shows SOLD OUT on site; Shopify says it's OOS. Match → no flag.
    guava_obs = VariantObservation(
        url="https://basedbodyworks.com/products/body-care-set",
        product_handle="body-care-set",
        variant_label="Guava Nectar + Guava Nectar",
        present=True,
        enabled=False,
        text="SOLD OUT",
    )
    assert _flags_for_observation(guava_obs, expected) == []

    # Santal variant shows SOLD OUT on site but Shopify says in stock → SALES LEAK.
    santal_obs = VariantObservation(
        url="https://basedbodyworks.com/products/body-care-set",
        product_handle="body-care-set",
        variant_label="Santal Sandalwood + Santal Sandalwood",
        present=True,
        enabled=False,
        text="SOLD OUT",
    )
    flags = _flags_for_observation(santal_obs, expected)
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.SALES_LEAK
    assert flags[0].variant_label == "Santal Sandalwood + Santal Sandalwood"


def test_flags_for_observation_defaults_to_default_variant_when_label_is_none(tmp_path):
    """Collection card observations have variant_label=None; match the
    product's default variant (e.g. Just One)."""
    sr = _sr(tmp_path)
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
    ]
    expected = compute_expected_products(products, sr)

    obs = VariantObservation(
        url="https://basedbodyworks.com/collections/all",
        product_handle="shampoo",
        variant_label=None,
        present=True,
        enabled=False,
        text="SOLD OUT",
    )
    flags = _flags_for_observation(obs, expected)
    # Default variant is "Just One" (qty 100, sellable); card shows SOLD OUT → SALES LEAK
    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.SALES_LEAK
    assert flags[0].variant_gid == "gid://shopify/ProductVariant/11"


def test_flags_for_observation_skips_unknown_handle(tmp_path):
    sr = _sr(tmp_path)
    expected = compute_expected_products([], sr)
    obs = VariantObservation(
        url="https://x.invalid/collections/all",
        product_handle="unknown",
        variant_label=None,
        present=True,
        enabled=True,
        text="ADD TO CART",
    )
    assert _flags_for_observation(obs, expected) == []


def test_flags_for_observation_skips_when_handle_is_none(tmp_path):
    sr = _sr(tmp_path)
    expected = compute_expected_products([], sr)
    obs = VariantObservation(
        url="https://x.invalid/pages/about",
        product_handle=None,
        variant_label=None,
        present=True,
        enabled=True,
        text="ADD TO CART",
    )
    assert _flags_for_observation(obs, expected) == []
