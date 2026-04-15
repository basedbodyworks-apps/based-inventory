"""Tests for quantity_alerts job block construction."""

from based_inventory.jobs.quantity_alerts import Alert, build_blocks


def test_build_blocks_renders_header_and_sections():
    alerts = [
        Alert(
            label="🚨 CRITICAL",
            product_title="Shampoo",
            qty=50,
            threshold=100,
            variant_info="`SHAMPOO-SINGLE`",
            mention_ids=["U1", "U2", "U3"],
            admin_url="https://admin.shopify.com/store/basedbodyworks/products/1",
            affected_sets=["Shower Duo", "Shower Essentials"],
        ),
    ]

    blocks = build_blocks(alerts)

    assert blocks[0]["type"] == "header"
    assert "Inventory Alert" in blocks[0]["text"]["text"]
    assert blocks[1]["type"] == "divider"

    section = blocks[2]
    assert section["type"] == "section"
    text = section["text"]["text"]
    assert "CRITICAL" in text
    assert "Shampoo" in text
    assert "50" in text
    assert "Shower Duo" in text

    footer = blocks[-1]
    assert footer["type"] == "context"
    assert "<@U1>" in footer["elements"][0]["text"]
    assert "<!channel>" in footer["elements"][0]["text"]
