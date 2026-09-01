from __future__ import annotations

import hashlib
import html
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from google import genai
from pydantic import BaseModel, Field, field_validator


# ============================================================
# CONFIGURAÇÃO
# ============================================================

MAX_PRICE = 2000.00
MIN_SIZE = 43.0
MAX_SIZE = 50.0

# Promoção comprovada:
MIN_VERIFIED_DISCOUNT = 15.0

# Exceção:
# preço muito agressivo + bom perfil gaming,
# mesmo sem histórico de preço.
EXCEPTIONAL_PRICE = 1600.00
EXCEPTIONAL_GAMING_SCORE = 75

# Só repetir alerta quando houver mudança relevante.
MIN_PRICE_DROP_FOR_REPEAT = 5.0

# Evita spam no Telegram.
MAX_ALERTS_PER_RUN = 3

# Limita a quantidade de ofertas que o Gemini precisa devolver.
MAX_SEARCH_RESULTS = 6

STATE_FILE = Path("state/sent_offers.json")

REQUEST_TIMEOUT = 30

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")


# ============================================================
# LOJAS CONFIÁVEIS
# ============================================================

TRUSTED_DOMAINS = {
    "mercadolivre.com.br": "Mercado Livre",
    "amazon.com.br": "Amazon Brasil",
    "magazineluiza.com.br": "Magazine Luiza",
    "magazineluiza.com": "Magazine Luiza",
    "casasbahia.com.br": "Casas Bahia",
    "kabum.com.br": "KaBuM",
    "americanas.com.br": "Americanas",
    "carrefour.com.br": "Carrefour",
    "fastshop.com.br": "Fast Shop",
    "pontofrio.com.br": "Ponto",
    "extra.com.br": "Extra",
    "leroymerlin.com.br": "Leroy Merlin",
}


# ============================================================
# BUSCA
# ============================================================

# Antes eram 6 pesquisas praticamente sobrepostas.
# Agora fazemos UMA pesquisa ampla e deixamos o Gemini
# encontrar tanto 43" quanto 50" e os recursos gaming.
SEARCH_QUERY = """
TV 4K 43 a 50 polegadas até R$ 2000 promoção gaming PS5 Brasil
"""


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
Você é um agente de compras extremamente criterioso.

OBJETIVO:
Encontrar promoções REAIS de TVs no Brasil para um comprador que usa PS5.
O foco é obter a melhor qualidade de imagem e os melhores recursos para
videogames dentro de R$ 2.000.

CRITÉRIOS OBRIGATÓRIOS:
- TV nova.
- Excluir usada, seminova, recondicionada, open box, outlet e peças.
- Tela entre 43 e 50 polegadas, inclusive.
- Resolução 4K/UHD nativa.
- Preço final máximo de R$ 2.000.
- Loja ou marketplace brasileiro confiável.
- URL deve apontar para a página específica do produto/oferta.
- Não aceitar categoria, artigo, lista ou página sem preço.
- Não inventar preço, especificação, disponibilidade ou URL.

GAMING:
Priorize, quando CONFIRMADOS por fonte:
1. 120 Hz ou mais
2. VRR
3. ALLM
4. HDMI 2.1
5. Game Mode / baixo input lag
6. HDR
7. bom upscaling/processamento
8. qualidade do painel

Se uma característica não estiver confirmada, use "não informado".

GTA V e GTA VI são referências de uso.
NUNCA diga que uma TV "roda GTA VI".
Quem executa o jogo é o console/PC; a TV recebe e exibe o sinal.

PREÇO:
O objetivo é encontrar uma oportunidade, não apenas uma TV barata.

Não considere automaticamente o preço "de X por Y" da loja como
histórico verdadeiro.

Quando possível, procure evidência independente de preço normal/histórico,
incluindo Buscapé, Zoom ou outras fontes de histórico.

Se não houver histórico confiável:
- reference_price_brl = null
- discount_percent = 0

Só informe desconto quando houver referência confiável.

Uma TV sem histórico ainda pode ser uma oportunidade excepcional se:
- preço <= R$ 1.600
- gaming_score >= 75

FONTES PRIORITÁRIAS:
Mercado Livre, Amazon Brasil, Magazine Luiza, Casas Bahia, KaBuM,
Americanas, Carrefour, Fast Shop, Ponto e Extra.

PESQUISA:
Faça UMA pesquisa ampla.
Encontre as melhores candidatas entre 43 e 50 polegadas.
Depois verifique as melhores candidatas nas páginas diretas quando possível.

Não desperdice pesquisas procurando separadamente cada tamanho ou recurso.

SAÍDA:
Retorne somente JSON válido conforme o schema.

Máximo de 6 ofertas.
Priorize qualidade da oportunidade.

Nunca invente informação.
"""


# ============================================================
# MODELOS
# ============================================================

class Offer(BaseModel):
    model: str
    brand: str
    size_inches: float
    resolution: str

    panel: str | None = None
    refresh_hz: int | None = None

    gaming_features: list[str] = Field(default_factory=list)

    price_brl: float
    reference_price_brl: float | None = None
    discount_percent: float = 0

    store: str
    url: str

    availability: str

    confidence: str
    confidence_reason: str

    gaming_score: int = Field(ge=0, le=100)

    evidence_urls: list[str] = Field(default_factory=list)

    notes: str = ""

    @field_validator("resolution")
    @classmethod
    def normalize_resolution(cls, value: str) -> str:
        return value.strip().upper()


class SearchResult(BaseModel):
    offers: list[Offer] = Field(
        default_factory=list,
        max_length=MAX_SEARCH_RESULTS,
    )


# ============================================================
# ESTADO
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": 2,
            "offers": {},
        }

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            raise ValueError("Estado inválido")

        # Compatibilidade com versão antiga.
        if isinstance(data.get("sent"), list):
            return {
                "version": 2,
                "offers": {
                    key: {
                        "price": None,
                    }
                    for key in data["sent"]
                },
            }

        offers = data.get("offers", {})

        if not isinstance(offers, dict):
            offers = {}

        return {
            "version": 2,
            "offers": offers,
        }

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return {
            "version": 2,
            "offers": {},
        }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    offers = state.get("offers", {})

    # Mantém somente os 1000 registros mais recentes.
    ordered = sorted(
        offers.items(),
        key=lambda item: (
            item[1].get("last_sent", "")
            if isinstance(item[1], dict)
            else ""
        ),
    )

    trimmed = dict(
        ordered[-1000:]
    )

    payload = {
        "version": 2,
        "offers": trimmed,
    }

    tmp = STATE_FILE.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(STATE_FILE)


# ============================================================
# URL
# ============================================================

def normalize_host(url: str) -> str:
    try:
        return (
            urlparse(url)
            .netloc
            .lower()
            .split(":")[0]
            .removeprefix("www.")
        )
    except Exception:
        return ""


def trusted_store(url: str) -> str | None:
    host = normalize_host(url)

    for domain, store in TRUSTED_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return store

    return None


def canonical_url(url: str) -> str:
    url = (
        (url or "")
        .strip()
        .replace("&amp;", "&")
    )

    parsed = urlparse(url)

    tracking_exact = {
        "gclid",
        "fbclid",
        "msclkid",
        "ref",
        "ref_",
        "tag",
        "ascsubtag",
        "camp",
        "campaign",
        "creative",
        "creativeasin",
        "spm",
    }

    kept = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ):
        key_lower = key.lower()

        if (
            key_lower.startswith("utm_")
            or key_lower in tracking_exact
        ):
            continue

        kept.append((key, value))

    query = urlencode(
        kept,
        doseq=True,
    )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query,
            "",
        )
    )


def product_key(offer: Offer) -> str:
    """
    Identidade do produto sem considerar o preço.
    Permite detectar queda de preço posteriormente.
    """

    raw = "|".join(
        [
            offer.brand.lower().strip(),
            offer.model.lower().strip(),
            normalize_host(offer.url),
            canonical_url(offer.url),
        ]
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


# ============================================================
# VALIDAÇÃO LOCAL
# ============================================================

def validate_offer(
    offer: Offer,
) -> Offer | None:

    # Tamanho.
    if not (
        MIN_SIZE
        <= offer.size_inches
        <= MAX_SIZE
    ):
        return None

    # Resolução.
    resolution = (
        offer.resolution
        .replace(" ", "")
        .upper()
    )

    if resolution not in {
        "4K",
        "UHD",
        "4KUHD",
    }:
        return None

    # Preço.
    if (
        offer.price_brl <= 0
        or offer.price_brl > MAX_PRICE
    ):
        return None

    # Disponibilidade.
    availability = (
        offer.availability
        .strip()
        .lower()
    )

    if availability not in {
        "disponível",
        "disponivel",
        "in stock",
        "em estoque",
        "available",
    }:
        return None

    # URL.
    if not offer.url.startswith(
        ("https://", "http://")
    ):
        return None

    # Loja.
    store = trusted_store(
        offer.url
    )

    if not store:
        return None

    # Normalização.
    offer.url = canonical_url(
        offer.url
    )

    offer.store = store

    offer.price_brl = round(
        float(offer.price_brl),
        2,
    )

    # Cálculo LOCAL do desconto.
    # Nunca confiamos no percentual fornecido pelo Gemini.
    if (
        offer.reference_price_brl
        is not None
        and offer.reference_price_brl > 0
    ):
        offer.reference_price_brl = round(
            float(offer.reference_price_brl),
            2,
        )

        calculated = (
            1
            - (
                offer.price_brl
                / offer.reference_price_brl
            )
        ) * 100

        offer.discount_percent = round(
            max(0, calculated),
            1,
        )

    else:
        offer.reference_price_brl = None
        offer.discount_percent = 0

    offer.gaming_score = max(
        0,
        min(
            100,
            int(offer.gaming_score),
        ),
    )

    # Limita evidências.
    offer.evidence_urls = [
        canonical_url(url)
        for url in offer.evidence_urls
        if (
            isinstance(url, str)
            and url.startswith(
                ("https://", "http://")
            )
        )
    ][:5]

    return offer


# ============================================================
# GEMINI
# ============================================================

def search_web() -> list[Offer]:
    api_key = os.environ["GEMINI_API_KEY"]

    client = genai.Client(
        api_key=api_key
    )

    prompt = (
        SYSTEM_PROMPT
        + "\n\nCONSULTA ÚNICA:\n"
        + SEARCH_QUERY
        + "\n\nEncontre e verifique somente as melhores "
          "candidatas antes de responder."
    )

    try:
        interaction = client.interactions.create(
            model=MODEL,
            input=prompt,
            tools=[
                {
                    "type": "google_search",
                },
                {
                    "type": "url_context",
                },
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": SearchResult.model_json_schema(),
            },
        )

    except Exception as exc:
        message = str(exc).lower()

        # NÃO repetir automaticamente em caso de quota.
        # Retry de quota pode piorar o consumo.
        if (
            "429" in message
            or "quota" in message
            or "too_many_requests" in message
            or "rate limit" in message
        ):
            print(
                "Gemini sem quota disponível nesta execução. "
                "Nenhuma nova tentativa será feita."
            )
            return []

        raise

    output = getattr(
        interaction,
        "output_text",
        None,
    )

    if not output:
        print(
            "Gemini não retornou conteúdo."
        )
        return []

    try:
        result = SearchResult.model_validate_json(
            output
        )
    except Exception as exc:
        print(
            f"Resposta do Gemini não pôde ser validada: {exc}"
        )
        return []

    return result.offers


# ============================================================
# REGRAS DE ALERTA
# ============================================================

def should_alert(
    offer: Offer,
) -> bool:

    # Promoção comprovada.
    if (
        offer.reference_price_brl is not None
        and offer.discount_percent
        >= MIN_VERIFIED_DISCOUNT
    ):
        return True

    # Exceção:
    # preço excepcional + bom perfil gaming.
    return (
        offer.price_brl
        <= EXCEPTIONAL_PRICE
        and offer.gaming_score
        >= EXCEPTIONAL_GAMING_SCORE
    )


def should_repeat_alert(
    offer: Offer,
    state: dict,
) -> bool:

    key = product_key(
        offer
    )

    previous = (
        state
        .get("offers", {})
        .get(key)
    )

    if not previous:
        return True

    old_price = (
        previous.get("price")
        if isinstance(
            previous,
            dict,
        )
        else None
    )

    if not isinstance(
        old_price,
        (int, float),
    ) or old_price <= 0:
        return False

    price_drop = (
        1
        - offer.price_brl
        / old_price
    ) * 100

    crossed_exceptional = (
        old_price > EXCEPTIONAL_PRICE
        >= offer.price_brl
    )

    old_discount = float(
        previous.get(
            "discount",
            0,
        )
    )

    improved_discount = (
        offer.discount_percent
        >= old_discount + 5
    )

    return (
        price_drop
        >= MIN_PRICE_DROP_FOR_REPEAT
        or crossed_exceptional
        or improved_discount
    )


def mark_sent(
    offer: Offer,
    state: dict,
) -> None:

    state.setdefault(
        "offers",
        {},
    )[product_key(offer)] = {
        "model": offer.model,
        "store": offer.store,
        "price": offer.price_brl,
        "discount": offer.discount_percent,
        "last_sent": now_iso(),
    }


# ============================================================
# PONTUAÇÃO
# ============================================================

def deal_score(
    offer: Offer,
) -> float:

    # Desconto: até 45 pontos.
    discount_component = (
        min(
            offer.discount_percent,
            40.0,
        )
        / 40.0
        * 45.0
    )

    # Gaming: até 40 pontos.
    gaming_component = (
        offer.gaming_score
        / 100.0
        * 40.0
    )

    # Preço: até 15 pontos.
    price_component = (
        max(
            0.0,
            1.0
            - offer.price_brl
            / MAX_PRICE,
        )
        * 15.0
    )

    return (
        discount_component
        + gaming_component
        + price_component
    )


# ============================================================
# TELEGRAM
# ============================================================

def brl(
    value: float,
) -> str:

    text = f"{value:,.2f}"

    return (
        "R$ "
        + text
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _tg(
    value: object,
) -> str:
    return html.escape(
        str(value),
        quote=False,
    )


def format_offer(
    offer: Offer,
) -> str:

    ref = (
        brl(
            offer.reference_price_brl
        )
        if offer.reference_price_brl
        else "não confirmado"
    )

    discount = (
        f"{offer.discount_percent:.1f}%"
        if offer.reference_price_brl
        else "não comprovado"
    )

    features = (
        ", ".join(
            offer.gaming_features
        )
        if offer.gaming_features
        else "não informado"
    )

    panel = (
        offer.panel
        or "não informado"
    )

    refresh = (
        f"{offer.refresh_hz} Hz"
        if offer.refresh_hz
        else "não informado"
    )

    confidence = (
        offer.confidence
        .upper()
    )

    evidence = ""

    if offer.evidence_urls:
        evidence = (
            "\n🔎 Evidência: "
            + _tg(
                offer.evidence_urls[0]
            )
        )

    return (
        "🔥 <b>OFERTA DE TV PARA GAMING</b>\n\n"

        f"📺 <b>{_tg(offer.brand)} "
        f"{_tg(offer.model)}</b>\n"

        f"📏 {offer.size_inches:g}\" • "
        f"{_tg(offer.resolution)} • "
        f"{_tg(panel)}\n"

        f"⚡ {_tg(refresh)}\n"

        f"🎮 {_tg(features)}\n\n"

        f"💰 <b>Agora: "
        f"{brl(offer.price_brl)}</b>\n"

        f"📊 Referência: {ref}\n"

        f"📉 Desconto real: "
        f"{discount}\n"

        f"🏪 {_tg(offer.store)}\n"

        f"🎯 Gaming: "
        f"{offer.gaming_score}/100\n"

        f"🛡️ {_tg(confidence)}\n\n"

        f"ℹ️ {_tg(offer.confidence_reason)}\n"

        f"📝 {_tg(offer.notes)}"

        f"{evidence}\n"

        f"\n🔗 {_tg(offer.url)}"
    )


def send_telegram(
    message: str,
) -> None:

    token = os.environ[
        "TOKEN_TELEGRAM"
    ]

    chat_id = os.environ[
        "TELEGRAM_CHAT_ID"
    ]

    endpoint = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    try:
        response = requests.post(
            endpoint,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 429:
            print(
                "Telegram atingiu rate limit. "
                "Alerta não será repetido nesta execução."
            )
            return

        response.raise_for_status()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Falha ao enviar Telegram: {exc}"
        ) from exc


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print(
        "=== BUSCADOR DE TVs PARA GAMING ==="
    )

    print(
        f"Faixa: {MIN_SIZE:g}\"–{MAX_SIZE:g}\""
    )

    print(
        f"Preço máximo: {brl(MAX_PRICE)}"
    )

    print(
        f"Modelo Gemini: {MODEL}"
    )

    state = load_state()

    raw_offers = search_web()

    print(
        f"Ofertas retornadas pelo Gemini: "
        f"{len(raw_offers)}"
    )

    validated: list[Offer] = []

    seen_products: set[str] = set()

    for raw in raw_offers:

        offer = validate_offer(
            raw
        )

        if offer is None:
            continue

        if not should_alert(
            offer
        ):
            continue

        key = product_key(
            offer
        )

        if key in seen_products:
            continue

        seen_products.add(key)

        validated.append(
            offer
        )

    validated.sort(
        key=deal_score,
        reverse=True,
    )

    print(
        f"Ofertas aprovadas: "
        f"{len(validated)}"
    )

    sent_count = 0

    for offer in validated:

        if sent_count >= MAX_ALERTS_PER_RUN:
            break

        if not should_repeat_alert(
            offer,
            state,
        ):
            continue

        send_telegram(
            format_offer(
                offer
            )
        )

        mark_sent(
            offer,
            state,
        )

        sent_count += 1

    save_state(
        state
    )

    print(
        "=== FINALIZADO ==="
    )

    print(
        f"Alertas enviados: "
        f"{sent_count}"
    )


if __name__ == "__main__":
    main()
