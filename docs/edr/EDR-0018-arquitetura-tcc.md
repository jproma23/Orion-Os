# EDR-0018 — Alinhamento da arquitetura à estrutura do TCC (SENTINELA X)

**Status:** Aprovado
**Data:** 2026-07-15
**Substitui parcialmente:** EDR-0005, EDR-0007, EDR-0009, EDR-0011

## Contexto

A especificação original do ORION OS (SES v1.0) definia o Notebook como
coordenador de dois enlaces independentes (Notebook↔Arduino e
Notebook↔Raspberry), com visão no Raspberry e navegação no Notebook.
O documento acadêmico do projeto (TCC — "Arquitetura Distribuída do
Projeto SENTINELA X") define uma arquitetura em cadeia, com papéis
diferentes. Para manter uma única fonte de verdade, a SES foi
realinhada à arquitetura do TCC.

## Decisão

Adotar a arquitetura em três camadas do TCC:

| Unidade | Papel | Nome oficial |
|---|---|---|
| Notebook | IA, visão computacional, reconhecimento facial, voz, memória, banco, planejamento de missão, interface, servidor | **Mission Core** |
| Raspberry Pi | Navegação, fusão de sensores, planejamento de trajetória, ponte de comunicação | **Motion Core** |
| Arduino Mega | Controle dos TB6600, motores, servos, MPU, ultrassons, encoders e demais sensores | **Hardware Core** |

**Topologia (em cadeia):**

- Notebook ↔ Raspberry Pi: **Ethernet (TCP)**.
- Raspberry Pi ↔ Arduino Mega: **USB Serial**.
- Notebook ↔ Arduino: **não existe enlace direto**.
- Opcional: USB direto Notebook ↔ Raspberry apenas para manutenção
  e diagnóstico.

## Consequências

1. A visão computacional (OpenCV/YOLO/reconhecimento facial) migra do
   Raspberry para o Notebook (`src/orion/vision/`).
2. A navegação (patrulha, seguimento, desvio, odometria) migra do
   Notebook para o Raspberry (`motion_core/`).
3. O firmware do Arduino passa a chamar-se Hardware Core
   (`firmware/hardware_core/`). Ele conversa apenas com o Raspberry.
4. O Arduino envia periodicamente ao Raspberry o pacote **Radar
   Inteligente** (ultrassons, MPU, estado dos motores, velocidade
   estimada); o Raspberry converte esses dados em eventos `motion.*`
   no Event Bus.
5. Comandos ao Arduino são de alto nível (MOVE_FORWARD, MOVE_DISTANCE,
   TURN_LEFT, TURN_RIGHT, STOP, DOCK, SCAN_FRONT, LIGHT_ON/OFF,
   RETURN_STATUS); toda a lógica de controle dos motores permanece
   encapsulada no Arduino.
6. **Autocalibração** (TCC, seção 7): na primeira inicialização o robô
   executa autoteste e um deslocamento controlado; a visão compara a
   distância real com a prevista e um fator de correção é armazenado.
7. A segurança em 3 camadas é preservada: reativa no Arduino, tática
   no Raspberry (Motion Core), estratégica no Notebook (Mission Core).

## Motivação

- Compatibilidade com o documento oficial do TCC.
- Redução de carga no Notebook (navegação em tempo quase-real no Pi).
- Ethernet elimina o gargalo de 115200 baud entre Notebook e Pi.
- O Arduino permanece determinístico e substituível.
