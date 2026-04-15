"""Tests for atc_audit Slack block construction."""

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
    assert "<!channel>" in footer
    assert "<@U08LCCS7V5Z>" in footer  # Carlos for OVERSELL
    assert "<@U0AHP969HC5>" in footer  # Ryan for SALES LEAK / NO BUY
