"""Tests for singles-variant resolution."""

from based_inventory.singles import resolve_single


def _variant(title, sku=None, qty=0, policy="DENY"):
    return {
        "id": f"gid://shopify/ProductVariant/{abs(hash(title)) % 10_000}",
        "title": title,
        "sku": sku,
        "inventoryQuantity": qty,
        "inventoryPolicy": policy,
        "inventoryItem": {
            "tracked": True,
            "inventoryLevels": [
                {"available": qty, "location": {"id": "L1", "name": "TX", "shipsInventory": True}}
            ],
        },
    }


def test_single_variant_product_returns_its_qty():
    p = {"title": "Scalp Scrubber", "variants": [_variant("Default Title", qty=5000)]}
    result = resolve_single(p)
    assert result.qty == 5000
    assert result.breakdown is None


def test_just_one_variant_among_packs():
    p = {
        "title": "Shampoo",
        "variants": [
            _variant("Just One", qty=3000),
            _variant("Two Pack", qty=200),
            _variant("Three Pack", qty=100),
        ],
    }
    result = resolve_single(p)
    assert result.qty == 3000


def test_full_size_variant():
    p = {"title": "Toiletry Bag", "variants": [_variant("Full Size", qty=750)]}
    result = resolve_single(p)
    assert result.qty == 750


def test_sku_contains_single():
    p = {
        "title": "Oddball",
        "variants": [
            _variant("Standard", sku="PROD-SINGLE", qty=42),
            _variant("Bundle", sku="PROD-BUNDLE", qty=999),
        ],
    }
    result = resolve_single(p)
    assert result.qty == 42


def test_multi_scent_sums_just_ones():
    p = {
        "title": "Body Wash",
        "variants": [
            _variant("Santal Sandalwood / Just One", qty=500),
            _variant("Santal Sandalwood / Two Pack", qty=50),
            _variant("Oud / Just One", qty=300),
            _variant("Oud / Two Pack", qty=40),
        ],
    }
    result = resolve_single(p)
    assert result.qty == 800
    assert result.breakdown is not None
    assert "Santal Sandalwood: 500" in result.breakdown
    assert "Oud: 300" in result.breakdown


def test_no_single_variant_falls_back_to_total():
    p = {
        "title": "Weird",
        "totalInventory": 123,
        "variants": [
            _variant("Two Pack", qty=999),
            _variant("Three Pack", qty=999),
        ],
    }
    result = resolve_single(p)
    assert result.qty == 123


def test_case_insensitive_matching():
    p = {"title": "X", "variants": [_variant("JUST ONE", qty=10)]}
    result = resolve_single(p)
    assert result.qty == 10
