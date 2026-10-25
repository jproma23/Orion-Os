# EDR-0035 — Rosto expressivo em camadas

**Data:** 13/09/2026
**Status:** Abandonada em 25/10/2026: a tela ESP32-S3 saiu do robô (o Notebook ficou no lugar dela, EDR-0036). O código do Pi que falava com a tela saiu no mesmo dia; o último estado dele está no commit `48286f5`, e o firmware em `~/orion_face` no Pi.
**Módulo:** Display Core (ESP32-S3 da tela)
**Relacionado:** EDR-0028 (fronteira), modelo emocional de oito estados

---

## Contexto

O rosto da LULU roda hoje no ESP32-S3 da tela de 3,5" e é acionado pelo modelo emocional de
oito estados (ALERTA → CAUTELOSO → TRISTE → SAUDOSO → CURIOSO → ORGULHOSO → ALEGRE →
ENTEDIADO).

Na prática isso funciona como uma chave de 8 posições: o rosto está inteiramente num estado
por vez. Aumentar a expressividade por esse caminho significa desenhar mais estados, o que
cresce linearmente em trabalho e não resolve o problema de fundo — o rosto parece **trocado**,
não **vivo**.

Um rosto vivo tem vários canais rodando ao mesmo tempo, em escalas temporais diferentes.

## Decisão

**O rosto passa a ser composto por quatro camadas independentes**, renderizadas juntas a
cada quadro. Nenhuma delas conhece as outras.

| Camada | Escala | Origem |
|---|---|---|
| **Emoção base** | segundos | Modelo de 8 estados, via Pi |
| **Direção do olhar** | ~100 ms | Bearing do alvo de atenção, via Pi |
| **Piscada** | autônoma | Timer local no S3, aleatório |
| **Micro-movimento** | contínuo | Local no S3 — respiração, deriva da pupila |

A riqueza vem da combinação. `ALERTA` + olhar à esquerda + piscada rápida é visivelmente
diferente de `ALERTA` + olhar parado, **sem desenhar um rosto novo**.

### Intensidade, não só categoria

Cada estado emocional ganha um valor contínuo de intensidade (0.0–1.0). `CURIOSO` fraco e
`CURIOSO` forte são rostos diferentes com o mesmo sprite base — abertura do olho, curvatura
da boca e amplitude do micro-movimento escalam com a intensidade.

Emoção real tem intensidade além de categoria. Oito estados binários não expressam isso.

### Olhar segue a atenção

**Item de maior retorno da decisão.** O bearing do obstáculo ou alvo mais próximo já existe
no sistema (LiDAR, ToF). O Pi envia esse ângulo; a pupila acompanha.

Um rosto que olha para a pessoa quando ela entra na sala comunica mais presença do que dez
expressões novas, e custa quase nada de código.

### Reação local, cognição acima

**O S3 reage a estímulo cru em menos de 100 ms, sem esperar o Pi.** Toque na tela,
proximidade súbita, som alto: o rosto responde imediatamente com uma reação local
(sobressalto, olhar na direção). A emoção "oficial" chega depois pelo Pi e assume.

É o mesmo princípio do EDR-0029 aplicado à expressão: **reflexo é local, cognição é acima**.
Um rosto que reage em 1 s parece slideshow; em 100 ms parece vivo. Timing importa mais que
qualidade de desenho.

### Antecipação

O rosto olha para o lado **antes** de o robô virar. O Pi informa a intenção de movimento
junto com o comando, não depois dele.

Antecipação lê como intenção; reação lê como susto. É um dos princípios clássicos de
animação e é barato de implementar aqui.

### Som não-verbal

O alto-falante já está na placa. Chirps e bipes curtos ligados à emoção e à intensidade,
independentes do TTS.

Princípio R2-D2: comunica estado sem falar. Muito mais barato que TTS, sem latência de API,
e funciona offline.

### Assimetria como identidade

O rosto atual tem um olho arredondado-quadrado e outro circular. **Isso é personagem, não
defeito** — mantido e explorado deliberadamente. Rosto simétrico genérico não dá identidade.

## Protocolo Pi → S3 (UART)

O Pi envia estado, não quadros. O S3 é dono da renderização.

| Campo | Conteúdo |
|---|---|
| `emocao` | um dos oito estados |
| `intensidade` | 0.0–1.0 |
| `olhar_bearing` | ângulo do alvo de atenção, ou nulo |
| `intencao` | direção do próximo movimento, para antecipação |

O S3 **nunca** recebe bitmap nem comando de pixel. Piscada e micro-movimento são gerados
localmente e não trafegam.

## Consequências

**Positivas**
- Tira a renderização inteira do Raspberry Pi — alívio direto no orçamento de CPU, que já
  está apertado com Nav2 e AMCL.
- Expressividade cresce de forma combinatória, não linear no número de sprites.
- O rosto continua vivo se o Pi estiver ocupado ou reiniciando: micro-movimento e piscada
  são locais.

**Negativas**
- Compor quatro camadas a cada quadro é mais pesado que trocar sprite. Verificar FPS no
  LVGL antes de aumentar a complexidade das camadas.
- Estado emocional agora existe em dois lugares (Pi e S3). O Pi é a autoridade; o S3 mantém
  só uma cópia de trabalho e nunca a persiste.

## Critério de aceite

- Um mesmo estado emocional produz rostos visivelmente distintos conforme olhar e intensidade
- Toque na tela gera reação visível em menos de 100 ms, medido
- Aproximar a mão de um ToF faz a pupila seguir o movimento
- O rosto continua animando (piscando, respirando) com a UART do Pi desconectada
- FPS estável no LVGL com as quatro camadas ativas

## Fora de escopo

Nada nesta decisão depende das fases de navegação. Pode ser implementada em paralelo,
a qualquer momento.
