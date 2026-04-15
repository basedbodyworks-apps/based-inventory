"""Sanity check: package imports without errors."""


def test_package_imports():
    import based_inventory

    assert based_inventory.__version__ == "0.1.0"
