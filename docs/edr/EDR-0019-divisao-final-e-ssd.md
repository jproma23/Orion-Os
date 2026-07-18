# EDR-0019 — Divisão final de responsabilidades, SSD no Raspberry e princípio offline-first

**Status:** Aprovado
**Data:** 2026-07-15
**Complementa:** EDR-0018
**Atualiza:** EDR-0008, EDR-0010, EDR-0012

## Contexto

Definido o hardware real: Notebook com 8 GB de RAM e Raspberry Pi 4
(4 GB) equipado com **SSD de 500 GB via USB 3**. No projeto anterior
(Sentinela X) um único Raspberry de 4 GB concentrava todas as funções;
a arquitetura distribuída permite alocar cada função no hardware mais
adequado. O SSD elimina a principal objeção a hospedar dados no Pi
(desgaste e corrupção do cartão SD sob escrita intensa).

## Decisão

| Unidade | Responsabilidades finais |
|---|---|
| **Notebook (Mission Core, 8 GB)** | IA (Ollama), planejamento de missão, visão computacional (OpenCV/YOLO/reconhecimento facial), voz (Whisper + Piper), avatar e multimídia na tela integrada |
| **Raspberry Pi 4 + SSD 500 GB (Motion Core)** | Navegação, fusão de sensores, ponte serial com o Arduino, **memória + aprendizado + banco SQLite no SSD**, **interface web** (dashboard, mapa, diagnóstico, configuração) |
| **Arduino Mega (Hardware Core)** | Motores, servos, sensores, segurança reativa |

Decisões complementares:

1. **Dimensionamento de IA para 8 GB**: modelo padrão `llama3.2:3b`
   (~2,5 GB) e `whisper base`. O total simultâneo (SO + IA + YOLO +
   Whisper + interface) fica em ~6 GB, evitando swap. Upgrade futuro
   de RAM permite voltar ao modelo 8B mudando apenas o `orion.yaml`.
2. **Banco no SSD do Raspberry**: SQLite WAL em `orion.db` no SSD;
   acesso exclusivo pelo Database Manager local; o Notebook consulta
   via API de memória pela Ethernet (rede local adiciona poucos ms,
   mantendo o requisito de contexto < 100 ms).
3. **Réplica cruzada de backup**: backup diário no SSD + cópia ao
   Notebook. Falha de qualquer um dos discos não apaga a memória do
   robô.
4. **Interface dividida**: avatar na tela do notebook (rosto do robô);
   interface web servida pelo Raspberry, acessível pelo IP local de
   qualquer dispositivo.
5. **Princípio offline-first, online-melhor**: toda função essencial
   opera sem Internet. Com Internet, habilitam-se acesso remoto
   (Raspberry Pi Connect), notificações ao celular e atualizações —
   sempre como camada opcional, nunca como dependência.

## Motivação

- Cada função no hardware onde rende mais: IA/visão no x86 com 8 GB;
  dados e serviços contínuos no Pi com SSD; tempo real no Arduino.
- A memória permanente vive no corpo do robô: trocar o Notebook não
  apaga o que o Fofão aprendeu.
- Dashboard ao lado do banco: consultas de histórico são locais ao SSD.
- SSD via USB 3 tem confiabilidade e desempenho adequados para escrita
  contínua de telemetria, ao contrário do cartão SD.

## Impacto

- Caps 1, 2, 3, 4, 6, 7, 11, 13, 15 e 16 atualizados.
- `config/orion.yaml`: modelo 3B, whisper base, caminhos do banco no
  SSD, seção `display.web` e seção `connectivity` (recursos online
  opcionais).
- Estrutura: `motion_core/` passa a conter também `memory/` e `webui/`.
