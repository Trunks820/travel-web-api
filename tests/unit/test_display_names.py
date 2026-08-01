import pytest

from src.db.models import AppUser
from src.profile.display_names import (
    DisplayNameError,
    former_name_digest,
    generate_default_display_name,
    normalize_display_name,
)


@pytest.mark.parametrize(
    ("raw_value", "presentation", "normalized"),
    [
        (" 山城Traveler_7 ", "山城Traveler_7", "山城traveler_7"),
        ("Ｆｏｏ", "Foo", "foo"),
        ("Été", "Été", "été"),
    ],
)
def test_normalize_display_name(raw_value, presentation, normalized):
    result = normalize_display_name(raw_value)
    assert result.presentation == presentation
    assert result.uniqueness_key == normalized


@pytest.mark.parametrize(
    ("raw_value", "code"),
    [
        ("1", "DISPLAY_NAME_INVALID"),
        ("1234", "DISPLAY_NAME_INVALID"),
        ("has space", "DISPLAY_NAME_INVALID"),
        ("emoji😀", "DISPLAY_NAME_INVALID"),
        ("管理员", "DISPLAY_NAME_RESERVED"),
        ("Admin_helper", "DISPLAY_NAME_RESERVED"),
        ("云途旅行", "DISPLAY_NAME_RESERVED"),
    ],
)
def test_invalid_and_reserved_display_names(raw_value, code):
    with pytest.raises(DisplayNameError, match=code):
        normalize_display_name(raw_value)


def test_default_display_name_contract():
    first = generate_default_display_name()
    second = generate_default_display_name()
    assert first.presentation.startswith("user_")
    assert len(first.presentation) == 15
    assert first.presentation == first.uniqueness_key
    assert first != second


def test_app_user_constructor_does_not_allocate_a_display_name():
    user = AppUser(public_id="usr_direct", status="ACTIVE", role="USER")

    assert user.display_name is None
    assert user.display_name_normalized is None
    assert user.display_name_changed_at is None


def test_former_name_digest_is_purpose_bound_and_keyed():
    first = former_name_digest("traveler", pepper="one")
    assert len(first) == 32
    assert first == former_name_digest("traveler", pepper="one")
    assert first != former_name_digest("traveler", pepper="two")
    assert b"traveler" not in first
