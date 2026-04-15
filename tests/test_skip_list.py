"""Tests for the hardcoded product skip list."""

from based_inventory.skip_list import should_skip


def test_shipping_protection_skipped():
    assert should_skip("BASED Shipping Protection") is True
    assert should_skip("Shipping") is True
    assert should_skip("Shipping International") is True


def test_membership_skipped():
    assert should_skip("Based Membership") is True


def test_legacy_products_skipped():
    assert should_skip("Based Shampoo 1.0") is True
    assert should_skip("Based Conditioner 2.0") is True
    assert should_skip("Hair Revival Serum") is True
    assert should_skip("Super Serum") is True
    assert should_skip("Showerhead Filter") is True


def test_samples_skipped():
    assert should_skip("4oz Shampoo + Conditioner Bundle Sample") is True
    assert should_skip("Shampoo + Conditioner Bundle Sample") is True


def test_tshirts_and_accessories_skipped():
    assert should_skip("Bath Stone (White)") is True
    assert should_skip("Based Wooden Comb - Light") is True
    assert should_skip("Brand Ambassador Package") is True


def test_active_products_not_skipped():
    assert should_skip("Shampoo") is False
    assert should_skip("Curl Cream") is False
    assert should_skip("Body Wash") is False
