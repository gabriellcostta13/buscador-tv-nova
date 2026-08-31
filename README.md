# 📺 Buscador TV Nova — Gaming

Bot automatizado para encontrar **promoções reais de TVs 4K de 43" a 50" por até R$ 2.000**, com prioridade para videogames e foco em qualidade de imagem/recursos úteis para PS5.

## 🎯 Objetivo

Encontrar uma TV que entregue a melhor experiência possível dentro de **R$ 2.000**, pensando em jogos como GTA V e no perfil de exigência de títulos futuros como GTA VI.

> A TV não "roda" GTA VI. O jogo é executado pelo console/PC; a TV recebe e exibe o sinal.

## Critérios obrigatórios

- 43" a 50"
- 4K/UHD nativo
- até R$ 2.000
- produto novo
- loja/marketplace brasileiro confiável
- página direta do produto/oferta

## Prioridade para gaming

O agente procura e pontua, quando confirmados:

1. 120 Hz ou mais
2. VRR
3. ALLM
4. HDMI 2.1
5. Game Mode / baixo input lag
6. HDR
7. processamento/upscaling
8. qualidade do painel

## 🧠 Como evita spam e falsas promoções

O sistema não envia simplesmente qualquer TV abaixo de R$ 2.000.

Uma oferta é elegível quando:

- existe desconto calculável de pelo menos **15%** sobre uma referência considerada confiável; ou
- não há histórico confiável, mas o preço é excepcional (**até R$ 1.600**) e a TV apresenta perfil gaming forte (**75/100 ou mais**).

O desconto é recalculado pelo Python. O preço "de/por" informado por uma loja não é aceito automaticamente como histórico.

O controle de duplicidade é inteligente:

- a mesma oferta não é reenviada no mesmo preço;
- uma queda de pelo menos **5%** pode gerar novo alerta;
- cruzar o limite de R$ 1.600 pode gerar novo alerta;
- uma melhora relevante do desconto também pode gerar novo alerta;
- no máximo **3 alertas por execução** são enviados.

## 🔎 Pesquisa

O Gemini usa a **Interactions API** com:

- Google Search para encontrar ofertas atuais;
- URL Context para verificar páginas candidatas quando possível;
- saída JSON estruturada validada com Pydantic.

O Python aplica uma segunda camada de validação para tamanho, resolução, preço, disponibilidade e domínio.

## 🔐 Secrets

Em **Settings → Secrets and variables → Actions**, configure:

- `GEMINI_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

Nunca coloque essas credenciais no código.

## ⏱️ Execução

O GitHub Actions executa o buscador aproximadamente a cada 30 minutos e também permite execução manual em **Actions → Buscador de TVs para Gaming → Run workflow**.

O GitHub pode atrasar horários agendados quando houver alta carga.

## Estrutura

```text
.
├── buscar_promocoes.py
├── requirements.txt
├── README.md
├── .gitignore
├── state/
│   └── sent_offers.json
├── tests/
│   └── test_buscar_promocoes.py
└── .github/
    └── workflows/
        └── agendamento.yml
```
