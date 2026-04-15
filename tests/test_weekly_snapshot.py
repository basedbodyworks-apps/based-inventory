"""Tests for weekly_snapshot block construction."""

from based_inventory.jobs.weekly_snapshot import ProductLine, build_snapshot_blocks


def test_snapshot_renders_categories():
    sections = [
        (
            "Hair Care",
            [
                ProductLine(name="Shampoo", qty=3000, breakdown=None, pack2=None, affected_sets=[]),
                ProductLine(
                    name="Conditioner",
                    qty=500,
                    breakdown=None,
                    pack2=250,
                    affected_sets=["Shower Duo"],
                ),
            ],
        ),
        (
            "Body",
            [
                ProductLine(
                    name="Body Wash",
                    qty=800,
                    breakdown="Santal: 500 · Oud: 300",
                    pack2=None,
                    affected_sets=["Body Care Set"],
                ),
            ],
        ),
    ]

    blocks = build_snapshot_blocks(sections, date_str="Apr 15, 2026")

    assert blocks[0]["type"] == "header"
    assert "Weekly Inventory Audit" in blocks[0]["text"]["text"]

    texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
    assert any("*Hair Care*" in t for t in texts)
    assert any("*Body*" in t for t in texts)
    combined = "\n".join(texts)
    assert "3,000" in combined
    assert "500" in combined
    assert "Shower Duo" in combined
    assert "Santal" in combined
    assert "2-pack: 250" in combined

    legend = blocks[-1]["elements"][0]["text"]
    assert "5K+" in legend
    assert "Oversold" in legend
