"""Tests for ATC state parsing from rendered HTML."""

from pathlib import Path

from based_inventory.crawl.atc_parser import AtcState, parse_atc_state

FIXTURES = Path(__file__).parent / "fixtures"


def test_in_stock_pdp_parsed_as_sellable():
    html = (FIXTURES / "pdp_in_stock.html").read_text()
    state = parse_atc_state(html)
    assert state == AtcState(present=True, enabled=True, text="Add to cart")


def test_sold_out_pdp_parsed_as_oos():
    html = (FIXTURES / "pdp_sold_out.html").read_text()
    state = parse_atc_state(html)
    assert state.present is True
    assert state.enabled is False
    assert "sold out" in state.text.lower()


def test_page_without_atc_element_returns_missing():
    html = (FIXTURES / "no_atc_element.html").read_text()
    state = parse_atc_state(html)
    assert state.present is False
    assert state.enabled is False
