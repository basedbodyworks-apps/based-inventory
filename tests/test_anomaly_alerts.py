"""Tests for anomaly_alerts: surface large non-shipping inventory_changes."""

from based_inventory.jobs.anomaly_alerts import (
    ANOMALY_THRESHOLD,
    Anomaly,
    _is_normal,
    _summarize_reason,
    build_blocks,
)

# --------------------------------------------------------------------------
# Reason classification
# --------------------------------------------------------------------------


def test_is_normal_recognizes_order_shipped() -> None:
    assert _is_normal('Order <a href="...">#577370194910744753</a> shipped.')


def test_is_normal_recognizes_kit_rollup() -> None:
    # Kit-rollup events are normal high-volume traffic; never flag.
    reason = (
        "Inventory updated because its kit sku\n        "
        '<a href="/dashboard/products/details/479413495">BB-CC-SINGLE</a> '
        "was updated.\n        Order shipped."
    )
    assert _is_normal(reason)


def test_is_normal_rejects_manual_dashboard_change() -> None:
    # The exact reason that fired in the 2026-05-07 curl cream probe.
    assert not _is_normal("Change from the product page via the ShipHero Web Dashboard.")


def test_is_normal_rejects_csv_upload_adjustment() -> None:
    assert not _is_normal("Inventory adjusted via CSV upload - 2026-05-01 stock count")


def test_is_normal_rejects_transfer() -> None:
    assert not _is_normal("Transferred to warehouse Wilshire")


# --------------------------------------------------------------------------
# Reason summarization for display
# --------------------------------------------------------------------------


def test_summarize_strips_html_and_collapses_whitespace() -> None:
    raw = (
        "\n        Inventory updated because its kit sku\n        "
        '<a href="/dashboard/products/details/479413495">BB-CC-SINGLE</a> '
        "was updated.\n        "
    )
    summary = _summarize_reason(raw)
    assert "<a" not in summary
    assert "  " not in summary  # collapsed whitespace
    assert "BB-CC-SINGLE" in summary


def test_summarize_truncates_at_120_chars() -> None:
    raw = "x" * 200
    summary = _summarize_reason(raw)
    assert len(summary) <= 121  # 120 chars + ellipsis
    assert summary.endswith("…")


def test_summarize_short_reason_unchanged_no_ellipsis() -> None:
    raw = "Manual adjustment"
    summary = _summarize_reason(raw)
    assert summary == "Manual adjustment"
    assert not summary.endswith("…")


# --------------------------------------------------------------------------
# Block rendering
# --------------------------------------------------------------------------


def _anomaly(**overrides) -> Anomaly:
    base = dict(
        sku="BB-CC-SINGLE",
        product_name="Curl Cream",
        change_in_on_hand=-3683,
        reason_short="Change from the product page via the ShipHero Web Dashboard.",
        reason_full="Change from the product page via the ShipHero Web Dashboard.",
        created_at="2026-05-07T11:30:00",
    )
    base.update(overrides)
    return Anomaly(**base)


def test_build_blocks_renders_header_and_summary() -> None:
    blocks = build_blocks([_anomaly()])
    assert blocks[0]["type"] == "header"
    assert "Anomaly" in blocks[0]["text"]["text"]
    summary = blocks[1]["text"]["text"]
    assert "1 non-shipping" in summary
    assert str(ANOMALY_THRESHOLD) in summary or f"{ANOMALY_THRESHOLD:,}" in summary


def test_build_blocks_renders_negative_change_with_minus_sign() -> None:
    blocks = build_blocks([_anomaly(change_in_on_hand=-3683)])
    body = next(
        b for b in blocks if b.get("type") == "section" and "Curl Cream" in b["text"]["text"]
    )
    text = body["text"]["text"]
    assert "−3,683" in text or "-3,683" in text  # noqa: RUF001


def test_build_blocks_renders_positive_change_with_plus_sign() -> None:
    blocks = build_blocks([_anomaly(change_in_on_hand=2500, sku="BB-XYZ", product_name="Test")])
    body = next(b for b in blocks if b.get("type") == "section" and "Test" in b["text"]["text"])
    text = body["text"]["text"]
    assert "+2,500" in text


def test_build_blocks_sorts_by_absolute_magnitude_desc() -> None:
    blocks = build_blocks(
        [
            _anomaly(sku="A", product_name="Small", change_in_on_hand=-600),
            _anomaly(sku="B", product_name="Huge", change_in_on_hand=-5000),
            _anomaly(sku="C", product_name="Medium", change_in_on_hand=-2000),
        ]
    )
    section_texts = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "section"
        and any(x in b["text"]["text"] for x in ("Small", "Medium", "Huge"))
    ]
    # Expect order: Huge (5000), Medium (2000), Small (600)
    assert section_texts[0].index("Huge") >= 0
    assert section_texts[1].index("Medium") >= 0
    assert section_texts[2].index("Small") >= 0


def test_build_blocks_includes_sku_and_reason() -> None:
    blocks = build_blocks([_anomaly(sku="BB-CC-SINGLE", reason_short="Manual stock adjustment")])
    body_text = "\n".join(b["text"]["text"] for b in blocks if b.get("type") == "section")
    assert "`BB-CC-SINGLE`" in body_text
    assert "Manual stock adjustment" in body_text


def test_build_blocks_renders_footer_with_threshold_and_window() -> None:
    blocks = build_blocks([_anomaly()])
    footer = blocks[-1]
    assert footer["type"] == "context"
    text = footer["elements"][0]["text"]
    assert str(ANOMALY_THRESHOLD) in text or f"{ANOMALY_THRESHOLD:,}" in text
    assert "24h" in text
    assert "ShipHero" in text
