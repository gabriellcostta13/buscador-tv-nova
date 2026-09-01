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


# =========================
# CONFIGURAÇÃO DO BUSCADOR
# =========================
MAX_PRICE = 2000.00
MIN_SIZE = 43.0
MAX_SIZE = 50.0
MIN_VERIFIED_DISCOUNT = 15.0
EXCEPTIONAL_PRICE = 1600.00
EXCEPTIONAL_GAMING_SCORE = 75
MIN_PRICE_DROP_FOR_REPEAT = 5.0
MAX_ALERTS_PER_RUN = 3
STATE_FILE = Path("state/sent_offers.json")
REQUEST_TIMEOUT = 30
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

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

SEARCH_QUERIES = [
    'TV 43 4K gaming preço promoção Brasil',
    'TV 50 4K gaming preço promoção Brasil',
    'TV 43 4K 120Hz VRR ALLM HDMI 2.1 promoção',
    'TV 50 4K 120Hz VRR ALLM HDMI 2.1 promoção',
    'TV 43 4K até R$ 2000 promoção',
    'TV 50 4K até R$ 2000 promoção',
]

SYSTEM_PROMPT = r"""
Você é um agente de compras extremamente criterioso. Sua tarefa é encontrar
PROMOÇÕES REAIS de TVs no mercado brasileiro para um comprador que usa PS5
para videogames. O objetivo é maximizar qualidade de imagem e recursos para
jogos dentro do limite de R$ 2.000.

========================
CRITÉRIOS INEGOCIÁVEIS
========================
- TV nova.
- Excluir usada, seminova, recondicionada, open box, outlet e peças.
- Tela de 43 a 50 polegadas, inclusive.
- Resolução 4K/UHD nativa.
- Preço final <= R$ 2.000.
- Loja/marketplace brasileiro confiável.
- A URL principal deve apontar para a página específica do produto/oferta.
- Não aceite apenas páginas de categoria, artigos, listas ou páginas sem preço.
- Não invente especificações, preço, disponibilidade ou URL.

========================
PRIORIDADE GAMING
========================
Dê maior pontuação para recursos que realmente importam em console:
1. 120 Hz ou mais;
2. VRR;
3. ALLM;
4. HDMI 2.1;
5. baixo input lag / Game Mode;
6. HDR de boa qualidade;
7. bom processamento/upscaling;
8. qualidade do painel.

Se uma característica não estiver confirmada por fonte confiável, escreva
"não informado" em vez de inferir.

GTA V e GTA VI são apenas referências do perfil de uso. NUNCA diga que uma
TV "roda GTA VI". Quem executa o jogo é o console/PC; a TV recebe e exibe o
sinal.

========================
PREÇO E DESCONTO
========================
O objetivo não é listar simplesmente TVs baratas. Queremos oportunidades.

- Não trate automaticamente o preço "de R$ X por R$ Y" da própria loja como
  histórico real.
- Procure evidência independente de preço normal/histórico quando possível.
- Sites como Buscapé, Zoom e páginas de histórico podem ser usados como
  evidência auxiliar.
- Se não houver evidência confiável para um preço de referência, use null em
  reference_price_brl e 0 em discount_percent.
- discount_percent deve representar apenas desconto calculável sobre uma
  referência confiável.
- Diferencie "preço baixo" de "desconto comprovado".
- Se o preço for excepcionalmente baixo, mas não houver histórico, isso pode
  ser classificado como oportunidade excepcional, mas a confiança deve refletir
  a ausência de histórico.

========================
PESQUISA E VERIFICAÇÃO
========================
Faça pesquisas separadas para 43", 50" e recursos gaming. Depois, para as
melhores candidatas, consulte a página direta do produto quando possível e
confirme preço, modelo, tamanho, resolução e disponibilidade.

Sempre que possível, use Google Search para encontrar fontes e URL Context
para verificar páginas candidatas. Não considere um resultado de busca isolado
como prova suficiente quando a página direta contradiz o resultado.

FONTES PRIORITÁRIAS:
Mercado Livre, Amazon Brasil, Magazine Luiza, Casas Bahia, KaBuM, Americanas,
Carrefour, Fast Shop, Ponto e Extra.

========================
SAÍDA
========================
Retorne SOMENTE JSON válido de acordo com o schema fornecido.
Máximo de 10 ofertas.
Ordene mentalmente pelas melhores oportunidades, mas o Python fará a ordenação
final.
"""


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
    offers: list[Offer] = Field(default_factory=list, max_length=10)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 2, "offers": {}}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Estado inválido")

        # Compatibilidade com a versão anterior, que armazenava apenas uma lista.
        if isinstance(data.get("sent"), list):
            return {"version": 2, "offers": {key: {"price": None} for key in data["sent"]}}

        offers = data.get("offers", {})
        if not isinstance(offers, dict):
            offers = {}
        return {"version": 2, "offers": offers}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"version": 2, "offers": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    offers = state.get("offers", {})
    # Limita o histórico aos 1000 registros mais recentes.
    ordered = sorted(
        offers.items(),
        key=lambda item: item[1].get("last_sent", "") if isinstance(item[1], dict) else "",
    )
    trimmed = dict(ordered[-1000:])
    payload = {"version": 2, "offers": trimmed}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def normalize_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    except Exception:
        return ""


def trusted_store(url: str) -> str | None:
    host = normalize_host(url)
    for domain, store in TRUSTED_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return store
    return None


def canonical_url(url: str) -> str:
    url = (url or "").strip().replace("&amp;", "&")
    parsed = urlparse(url)
    tracking_exact = {
        "gclid", "fbclid", "msclkid", "ref", "ref_", "tag", "ascsubtag",
        "camp", "campaign", "creative", "creativeasin", "spm",
    }
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in tracking_exact:
            continue
        kept.append((key, value))
    query = urlencode(kept, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))


def product_key(offer: Offer) -> str:
    """Identidade do produto/oferta sem preço, para controlar repetição inteligente."""
    raw = "|".join(
        [
            offer.brand.lower().strip(),
            offer.model.lower().strip(),
            normalize_host(offer.url),
            canonical_url(offer.url),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate_offer(offer: Offer) -> Offer | None:
    if not (MIN_SIZE <= offer.size_inches <= MAX_SIZE):
        return None
    if offer.resolution.replace(" ", "") not in {"4K", "UHD", "4KUHD"}:
        return None
    if offer.price_brl <= 0 or offer.price_brl > MAX_PRICE:
        return None
    if offer.availability.strip().lower() not in {
        "disponível", "disponivel", "in stock", "em estoque", "available"
    }:
        return None
    if not offer.url.startswith(("https://", "http://")):
        return None

    store = trusted_store(offer.url)
    if not store:
        return None

    offer.url = canonical_url(offer.url)
    offer.store = store
    offer.price_brl = round(float(offer.price_brl), 2)

    if offer.reference_price_brl is not None and offer.reference_price_brl > 0:
        offer.reference_price_brl = round(float(offer.reference_price_brl), 2)
        calculated = (1 - offer.price_brl / offer.reference_price_brl) * 100
        offer.discount_percent = round(max(0, calculated), 1)
    else:
        offer.reference_price_brl = None
        offer.discount_percent = 0

    offer.gaming_score = max(0, min(100, int(offer.gaming_score)))
    offer.evidence_urls = [
        canonical_url(url)
        for url in offer.evidence_urls
        if isinstance(url, str) and url.startswith(("https://", "http://"))
    ][:5]
    return offer


def search_web() -> list[Offer]:
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    query_text = "\n".join(f"- {query}" for query in SEARCH_QUERIES)
    prompt = (
        SYSTEM_PROMPT
        + "\n\nCONSULTAS INICIAIS:\n"
        + query_text
        + "\n\nEncontre e verifique as melhores candidatas antes de responder."
    )

    interaction = client.interactions.create(
        model=MODEL,
        input=prompt,
        tools=[
            {"type": "google_search"},
            {"type": "url_context"},
        ],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": SearchResult.model_json_schema(),
        },
    )

    return SearchResult.model_validate_json(interaction.output_text).offers


def should_alert(offer: Offer) -> bool:
    # Regra principal: promoção comprovada.
    if offer.reference_price_brl is not None and offer.discount_percent >= MIN_VERIFIED_DISCOUNT:
        return True

    # Exceção controlada: preço realmente agressivo + bom perfil gaming.
    return offer.price_brl <= EXCEPTIONAL_PRICE and offer.gaming_score >= EXCEPTIONAL_GAMING_SCORE


def should_repeat_alert(offer: Offer, state: dict) -> bool:
    key = product_key(offer)
    previous = state.get("offers", {}).get(key)
    if not previous:
        return True

    old_price = previous.get("price") if isinstance(previous, dict) else None
    if not isinstance(old_price, (int, float)) or old_price <= 0:
        return False

    drop = (1 - offer.price_brl / old_price) * 100
    crossed_exceptional = old_price > EXCEPTIONAL_PRICE >= offer.price_brl
    improved_discount = offer.discount_percent >= float(previous.get("discount", 0)) + 5

    return drop >= MIN_PRICE_DROP_FOR_REPEAT or crossed_exceptional or improved_discount


def mark_sent(offer: Offer, state: dict) -> None:
    state.setdefault("offers", {})[product_key(offer)] = {
        "model": offer.model,
        "store": offer.store,
        "price": offer.price_brl,
        "discount": offer.discount_percent,
        "last_sent": now_iso(),
    }


def brl(value: float) -> str:
    text = f"{value:,.2f}"
    return "R$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def _tg(value: object) -> str:
    return html.escape(str(value), quote=False)


def format_offer(offer: Offer) -> str:
    ref = brl(offer.reference_price_brl) if offer.reference_price_brl else "não confirmado"
    discount = f"{offer.discount_percent:.1f}%" if offer.reference_price_brl else "não comprovado"
    features = ", ".join(offer.gaming_features) if offer.gaming_features else "não informado"
    panel = offer.panel or "não informado"
    refresh = f"{offer.refresh_hz} Hz" if offer.refresh_hz else "não informado"
    confidence = offer.confidence.upper()

    return (
        "🔥 <b>OFERTA DE TV PARA GAMING</b>\n\n"
        f"📺 <b>{_tg(offer.brand)} {_tg(offer.model)}</b>\n"
        f"📏 {offer.size_inches:g}\" • {_tg(offer.resolution)} • {_tg(panel)}\n"
        f"⚡ {_tg(refresh)}\n"
        f"🎮 {_tg(features)}\n\n"
        f"💰 <b>Agora: {brl(offer.price_brl)}</b>\n"
        f"📊 Referência: {ref}\n"
        f"📉 Desconto real calculado: {discount}\n"
        f"🏪 {_tg(offer.store)}\n"
        f"🎯 Gaming: {offer.gaming_score}/100\n"
        f"🛡️ {_tg(confidence)}\n\n"
        f"ℹ️ {_tg(offer.confidence_reason)}\n"
        f"📝 {_tg(offer.notes)}\n"
        + (f"🔎 Evidência: {_tg(offer.evidence_urls[0])}\n" if offer.evidence_urls else "")
        + f"\n🔗 {_tg(offer.url)}"
    )


def send_telegram(message: str) -> None:
    token = os.environ["TOKEN_TELEGRAM"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    last_error: Exception | None = None
    for attempt in range(3):
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
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    retry_after = 5
                time.sleep(min(max(retry_after, 1), 60))
                continue

            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)

    raise RuntimeError(f"Falha ao enviar Telegram após 3 tentativas: {last_error}")


def deal_score(offer: Offer) -> float:
    """Pontuação final: equilíbrio entre desconto, adequação gaming e preço."""
    discount_component = min(offer.discount_percent, 40.0) / 40.0 * 45.0
    gaming_component = offer.gaming_score / 100.0 * 40.0
    price_component = max(0.0, 1.0 - offer.price_brl / MAX_PRICE) * 15.0
    return discount_component + gaming_component + price_component


def main() -> None:
    state = load_state()
    raw_offers = search_web()

    validated: list[Offer] = []
    seen_products: set[str] = set()

    for raw in raw_offers:
        offer = validate_offer(raw)
        if offer is None or not should_alert(offer):
            continue

        key = product_key(offer)
        if key in seen_products:
            continue
        seen_products.add(key)
        validated.append(offer)

    validated.sort(key=deal_score, reverse=True)

    sent_count = 0
    for offer in validated:
        if sent_count >= MAX_ALERTS_PER_RUN:
            break
        if not should_repeat_alert(offer, state):
            continue

        send_telegram(format_offer(offer))
        mark_sent(offer, state)
        sent_count += 1

    save_state(state)
    print(
        f"Pesquisadas: {len(raw_offers)} | "
        f"Aprovadas: {len(validated)} | "
        f"Novos alertas: {sent_count}"
    )


if __name__ == "__main__":
    main()
