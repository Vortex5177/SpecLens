"""Tests that override dependencies the legacy 0.110 way."""
from main import app, get_token_header


def fake_header():
    return {"token": "fake"}


def test_items_with_override():
    # Legacy style: mutate app.dependency_overrides directly
    app.dependency_overrides[get_token_header] = fake_header
    try:
        assert get_token_header in app.dependency_overrides
    finally:
        app.dependency_overrides.clear()