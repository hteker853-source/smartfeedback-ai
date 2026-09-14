from app.phones import normalize_phone, region_from_phone, validate_phone


def test_normalize_variants():
    assert normalize_phone("05445974126") == "+905445974126"
    assert normalize_phone("+905445974126") == "+905445974126"
    assert normalize_phone("00905445974126") == "+905445974126"
    assert normalize_phone(" 0544 597 41 26 ") == "+905445974126"


def test_region_detection():
    assert region_from_phone("+905445974126") == "TR"
    assert region_from_phone("+14155550100") == "US"
    assert region_from_phone("+491234567") == "DE"


def test_validate_valid_us():
    ok, norm, err = validate_phone("+14155550100", {"US"})
    assert ok is True
    assert norm == "+14155550100"
    assert err == ""


def test_validate_unsupported_region_rejected():
    ok, norm, err = validate_phone("+905445974126", {"US"})
    assert ok is False
    assert "TR" in err


def test_validate_empty_and_garbage():
    assert validate_phone("", {"US"})[0] is False
    assert validate_phone("abc", {"US"})[0] is False
    assert validate_phone("0532...", {"US"})[0] is False


def test_validate_no_restriction_allows_tr():
    ok, norm, err = validate_phone("05445974126", set())
    assert ok is True
    assert norm == "+905445974126"


def test_validate_invalid_length():
    ok, norm, err = validate_phone("+905", {"US"})
    assert ok is False
