"""Tests for atc_audit Slack block construction."""

import dataclasses

from based_inventory.crawl.diff import Flag, FlagType
from based_inventory.jobs.atc_audit import build_atc_blocks


def test_atc_blocks_includes_all_flag_types_and_v0_footer():
    flags = [
        Flag(
            flag_type=FlagType.SALES_LEAK,
            product_title="Curl Cream",
            variant_gid="gid1",
            variant_label="Two Pack",
            url="https://basedbodyworks.com/products/curl-cream",
            expected_sellable=True,
            observed_text="Sold out",
            state_key="gid1::...::SALES_LEAK",
        ),
        Flag(
            flag_type=FlagType.OVERSELL_RISK,
            product_title="Shower Duo",
            variant_gid="gid2",
            variant_label=None,
            url="https://basedbodyworks.com/products/shower-duo",
            expected_sellable=False,
            observed_text="Add to cart",
            state_key="gid2::...::OVERSELL_RISK",
        ),
        Flag(
            flag_type=FlagType.NO_BUY_BUTTON,
            product_title="Leave-In Conditioner",
            variant_gid="gid3",
            variant_label=None,
            url="https://basedbodyworks.com/pages/spring-launch",
            expected_sellable=True,
            observed_text="",
            state_key="gid3::...::NO_BUY_BUTTON",
        ),
    ]

    blocks = build_atc_blocks(flags)

    texts = "\n".join(
        b["text"]["text"] for b in blocks if b.get("type") == "section" and "text" in b
    )
    assert "SALES LEAK" in texts
    assert "Curl Cream" in texts
    assert "OVERSELL RISK" in texts
    assert "Shower Duo" in texts
    assert "NO BUY BUTTON" in texts
    assert "Leave-In Conditioner" in texts

    # v0 limitation footer on OVERSELL RISK rows
    assert "v0 limitation" in texts
    assert "ShipHero" in texts

    footer = blocks[-1]["elements"][0]["text"]
    assert "<@" not in footer
    assert "<!channel>" not in footer


def test_dedupe_flags_by_state_key():
    from based_inventory.jobs.atc_audit import _dedupe_flags_by_state_key

    base = Flag(
        flag_type=FlagType.SALES_LEAK,
        product_title="Shampoo",
        variant_gid="gid1",
        variant_label=None,
        url="https://x/products/shampoo",
        expected_sellable=True,
        observed_text="Sold out",
        state_key="gid1::https://x/products/shampoo::SALES_LEAK",
    )
    duplicate = dataclasses.replace(base, variant_label="Just One")
    other = Flag(
        flag_type=FlagType.OVERSELL_RISK,
        product_title="Conditioner",
        variant_gid="gid2",
        variant_label=None,
        url="https://x/products/conditioner",
        expected_sellable=False,
        observed_text="Add to cart",
        state_key="gid2::https://x/products/conditioner::OVERSELL_RISK",
    )

    result = _dedupe_flags_by_state_key([base, duplicate, other])
    assert len(result) == 2
    assert result[0].variant_label is None  # first occurrence kept
    assert result[1].state_key == other.state_key


def test_compute_expected_states_covers_singles_packs_sets_and_backorder(tmp_path):
    import json

    from based_inventory.jobs.atc_audit import compute_expected_states
    from based_inventory.sets import SetResolver

    components_file = tmp_path / "sc.json"
    components_file.write_text(json.dumps({"sets": {"Shower Duo": ["Shampoo", "Conditioner"]}}))
    sr = SetResolver(components_path=components_file)

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
        {
            "id": "gid://shopify/Product/4",
            "title": "Preorder Thing",
            "handle": "preorder",
            "totalInventory": 0,
            "variants": [
                _variant("gid://shopify/ProductVariant/41", "Just One", 0, policy="CONTINUE")
            ],
        },
    ]

    expected = compute_expected_states(products, sr)

    # Shampoo Just One: 100 singles from inventory levels, sellable
    assert expected["gid://shopify/ProductVariant/11"].expected.sellable is True
    # Shampoo Two Pack: 100 singles // 2 = 50 >= 1, sellable
    assert expected["gid://shopify/ProductVariant/12"].expected.sellable is True

    # Conditioner Just One: 0 qty in inventory levels, not sellable
    assert expected["gid://shopify/ProductVariant/21"].expected.sellable is False

    # Shower Duo: min(Shampoo=100, Conditioner=0) = 0, not sellable
    assert expected["gid://shopify/ProductVariant/31"].expected.sellable is False

    # Preorder Thing: qty 0 but inventoryPolicy=CONTINUE.
    # compute_expected_states sets sellable based on inventory math (False here);
    # the CONTINUE policy is passed through for generate_flags to consume.
    assert expected["gid://shopify/ProductVariant/41"].expected.sellable is False
    assert expected["gid://shopify/ProductVariant/41"].expected.inventory_policy == "CONTINUE"
