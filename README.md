# 📺 Buscador TV Nova — Gaming

Bot automatizado para encontrar promoções reais de TVs 4K de 43" a 50" por até **R$ 2.300**, com prioridade para videogames e PS5.

## Critérios obrigatórios
- 43" a 50"
- 4K/UHD nativo
- até R$ 2.300
- produto novo
- loja/marketplace brasileiro confiável
- página direta do produto/oferta

## Prioridade gaming
1. 120 Hz ou mais
2. VRR
3. ALLM
4. HDMI 2.1
5. Game Mode / baixo input lag
6. HDR
7. processamento/upscaling
8. qualidade do painel

## Elegibilidade
- desconto calculável de pelo menos **12%** sobre referência confiável; ou
- sem histórico confiável: preço **até R$ 1.800** e gaming score **70/100 ou mais**.

O desconto é recalculado pelo Python.

## Controle de spam
- mesma oferta não é reenviada no mesmo preço;
- queda de pelo menos 5% pode gerar novo alerta;
- melhora relevante do desconto pode gerar novo alerta;
- máximo de 3 alertas por execução.

## Secrets
Configure exatamente:
- `GEMINI_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Execução
O GitHub Actions executa aproximadamente às **09:10, 15:10 e 21:10 BRT**, além de permitir execução manual.

> A TV não "roda" GTA VI. O jogo é executado pelo console/PC; a TV recebe e exibe o sinal.
