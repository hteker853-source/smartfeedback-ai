from app.analyzer import analyze_feedback, detect_missing_products


def test_missing_product_detection_tr():
    missing = detect_missing_products("Ayran sipariş etmiştik ama gelmedi.", "tr")
    assert any(m.name == "ayran" for m in missing)


def test_missing_product_detection_en():
    missing = detect_missing_products("The fries were missing from my order.", "en")
    assert len(missing) >= 1


def test_sentiment_negative_tr():
    fb = analyze_feedback("Yemek çok soğuk geldi, çok kötüydü, memnun değilim.", "tr")
    assert fb.sentiment == "negative"
    assert fb.satisfaction <= 2


def test_sentiment_positive_tr():
    fb = analyze_feedback("Yemek harikaydı, çok lezzetli, teşekkürler.", "tr")
    assert fb.sentiment == "positive"
    assert fb.satisfaction >= 4


def test_missing_product_marks_alert_priority():
    fb = analyze_feedback("Ayran gelmedi.", "tr")
    assert fb.missing_products
    assert fb.urgency in ("critical", "high")
    assert fb.priority in ("critical", "high")
