from buscar_promocoes import (
    MAX_PRICE,
    Offer,
    canonical_url,
    format_offer,
    product_key,
    should_alert,
    should_repeat_alert,
    validate_offer,
)


def make_offer(**overrides):
    data = {
        "model": "Modelo Teste 50",
        "brand": "Marca",
        "size_inches": 50,
        "resolution": "4K",
        "panel": "LED",
        "refresh_hz": 120,
        "gaming_features": ["ALLM", "VRR"],
        "price_brl": 1600,
        "reference_price_brl": 2000,
        "discount_percent": 20,
        "store": "Amazon Brasil",
        "url": "https://www.amazon.com.br/dp/TESTE?utm_source=x",
        "availability": "disponível",
        "confidence": "ALTA",
        "confidence_reason": "Preço e especificações encontrados em fontes confiáveis.",
        "gaming_score": 90,
        "evidence_urls": ["https://www.zoom.com.br/teste"],
        "notes": "Oferta de teste.",
    }
    data.update(overrides)
    return Offer(**data)


def test_valid_offer_is_accepted():
    offer = validate_offer(make_offer())
    assert offer is not None
    assert offer.price_brl == 1600
    assert offer.discount_percent == 20.0
    assert offer.store == "Amazon Brasil"


def test_size_outside_range_is_rejected():
    assert validate_offer(make_offer(size_inches=55)) is None


def test_price_above_limit_is_rejected():
    assert validate_offer(make_offer(price_brl=MAX_PRICE + 0.01)) is None


def test_non_trusted_store_is_rejected():
    assert validate_offer(make_offer(url="https://site-suspeito.example/produto")) is None


def test_discount_is_calculated_locally():
    offer = validate_offer(make_offer(price_brl=1500, reference_price_brl=2000, discount_percent=99))
    assert offer is not None
    assert offer.discount_percent == 25.0


def test_exceptional_gaming_price_can_alert_without_history():
    offer = validate_offer(
        make_offer(price_brl=1500, reference_price_brl=None, discount_percent=0, gaming_score=80)
    )
    assert offer is not None
    assert should_alert(offer)


def test_normal_price_without_history_does_not_alert():
    offer = validate_offer(
        make_offer(price_brl=1900, reference_price_brl=None, discount_percent=0, gaming_score=70)
    )
    assert offer is not None
    assert not should_alert(offer)


def test_canonical_url_removes_tracking():
    url = canonical_url(
        "https://www.amazon.com.br/dp/ABC?tag=abc&utm_source=google&foo=bar#top"
    )
    assert url == "https://www.amazon.com.br/dp/ABC?foo=bar"


def test_product_key_ignores_price():
    first = product_key(make_offer(price_brl=1600))
    second = product_key(make_offer(price_brl=1500))
    assert first == second


def test_repeat_alert_requires_meaningful_price_drop():
    offer = validate_offer(make_offer(price_brl=1500, reference_price_brl=None, discount_percent=0))
    assert offer is not None
    state = {"offers": {product_key(offer): {"price": 1600, "discount": 0, "last_sent": "x"}}}
    assert should_repeat_alert(offer, state)


def test_small_price_change_does_not_repeat():
    offer = validate_offer(make_offer(price_brl=1580, reference_price_brl=None, discount_percent=0))
    assert offer is not None
    state = {"offers": {product_key(offer): {"price": 1600, "discount": 0, "last_sent": "x"}}}
    assert not should_repeat_alert(offer, state)


def test_message_contains_core_data():
    message = format_offer(make_offer())
    assert "R$ 1.600,00" in message
    assert '50"' in message
    assert "120 Hz" in message
    assert "🔗 https://www.amazon.com.br/dp/TESTE" in message
