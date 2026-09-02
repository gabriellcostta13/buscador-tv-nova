from buscar_promocoes import (
    EXCEPTIONAL_GAMING_SCORE, EXCEPTIONAL_PRICE, MAX_PRICE,
    MAX_SIZE, MIN_SIZE, MIN_VERIFIED_DISCOUNT,
    canonical_url, format_offer, Offer,
    product_key, should_alert, should_repeat_alert, validate_offer,
)

def make_offer(**overrides):
    data = {
        "model": "Modelo Teste 50", "brand": "Marca", "size_inches": 50,
        "resolution": "4K", "panel": "LED", "refresh_hz": 120,
        "gaming_features": ["ALLM", "VRR"], "price_brl": 1600,
        "reference_price_brl": 2000, "discount_percent": 20,
        "store": "Amazon Brasil", "url": "https://www.amazon.com.br/dp/TESTE?utm_source=x",
        "availability": "disponível", "confidence": "ALTA",
        "confidence_reason": "Preço e especificações encontrados em fontes confiáveis.",
        "gaming_score": 90, "evidence_urls": ["https://www.zoom.com.br/teste"],
        "notes": "Oferta de teste.",
    }
    data.update(overrides)
    return Offer(**data)

def test_current_rules():
    assert (MAX_PRICE, MIN_SIZE, MAX_SIZE) == (2300.0, 43.0, 50.0)
    assert (MIN_VERIFIED_DISCOUNT, EXCEPTIONAL_PRICE, EXCEPTIONAL_GAMING_SCORE) == (12.0, 1800.0, 70)

def test_size_boundaries():
    assert validate_offer(make_offer(size_inches=43)) is not None
    assert validate_offer(make_offer(size_inches=50)) is not None
    assert validate_offer(make_offer(size_inches=42.99)) is None
    assert validate_offer(make_offer(size_inches=50.01)) is None

def test_price_boundaries():
    assert validate_offer(make_offer(price_brl=2300)) is not None
    assert validate_offer(make_offer(price_brl=2300.01)) is None

def test_discount_is_calculated_locally():
    offer = validate_offer(make_offer(price_brl=1500, reference_price_brl=2000, discount_percent=99))
    assert offer is not None
    assert offer.discount_percent == 25.0

def test_exceptional_boundaries():
    good = validate_offer(make_offer(price_brl=1800, reference_price_brl=None, gaming_score=70))
    bad_price = validate_offer(make_offer(price_brl=1800.01, reference_price_brl=None, gaming_score=70))
    bad_score = validate_offer(make_offer(price_brl=1800, reference_price_brl=None, gaming_score=69))
    assert good is not None and should_alert(good)
    assert bad_price is not None and not should_alert(bad_price)
    assert bad_score is not None and not should_alert(bad_score)

def test_non_trusted_store_is_rejected():
    assert validate_offer(make_offer(url="https://site-suspeito.example/produto")) is None

def test_canonical_url_removes_tracking():
    assert canonical_url("https://www.amazon.com.br/dp/ABC?tag=abc&utm_source=google&foo=bar#top") == "https://www.amazon.com.br/dp/ABC?foo=bar"

def test_product_key_ignores_price():
    assert product_key(make_offer(price_brl=1600)) == product_key(make_offer(price_brl=1500))

def test_repeat_alert_logic():
    offer = validate_offer(make_offer(price_brl=1500, reference_price_brl=None, gaming_score=80))
    state = {"offers": {product_key(offer): {"price": 1600, "discount": 0, "last_sent": "x"}}}
    assert offer is not None and should_repeat_alert(offer, state)

def test_message_contains_core_data():
    message = format_offer(make_offer())
    assert "R$ 1.600,00" in message
    assert '50"' in message
    assert "120 Hz" in message
    assert "https://www.amazon.com.br/dp/TESTE" in message

