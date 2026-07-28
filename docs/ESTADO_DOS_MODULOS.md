# ORION OS — Mapa de saúde dos módulos

**Snapshot de:** 2026-07-27 (commit `0dc2b09`)
**Regra de atualização:** este documento é vivo. A cada correção que segue o
fluxo de 7 etapas de `ARQUITETURA.txt` ("Fluxo de trabalho"), o módulo tocado
muda de categoria aqui — na mesma sessão, não depois. Objetivo: o sistema
inteiro chegar a 🟢.

## Categorias

- 🟢 **Estável** — testado (unitário **e** hardware) e em uso sem problema conhecido.
- 🟡 **Em validação** — implementado, testes unitários passam, falta a etapa 5
  (testes no hardware).
- 🟠 **Em desenvolvimento** — funciona parcialmente; escopo mínimo deliberado
  ou implementação incompleta, e isso está documentado no próprio código.
- 🔴 **Pendente** — não implementado, ou implementado com problema conhecido
  e não corrigido.

Cada linha tem a **evidência** que sustenta a categoria — não é opinião.

---

## Mission Core (Notebook) — `src/orion/`

| Módulo | Categoria | Evidência |
|---|---|---|
| `kernel/` (event_bus, registry, watchdog, boot, config, logger) | 🟢 | Fase 1, mais antiga do projeto. `python -m orion --sim` chega a `system.ready` e encerra limpo. Testado (`test_event_bus`, `test_registry`, `test_watchdog`, `test_boot`, `test_config`, `test_modulos_boot`). |
| `communication/` (crc, framing, transport, service, discovery, heartbeat) | 🟢 | O módulo mais maduro do projeto. CRC16 validado byte a byte em Python **e** C++. **Validado em produção real no Pi** (25/07): 3 reconexões seguidas → sempre 1 socket ESTABLISHED, `comm.module_lost` publicado uma vez por perda. |
| `mission/` (ai_manager, mission_planner, grounding, diario) | 🟢 | Fallback OpenAI→Ollama funcionando em produção (log: "OpenRouter 200 OK"). O bug de teste de 27/07 (`conselheiro_comportamento` testava Ollama, código usa OpenAI desde EDR-0021) foi corrigido sem tocar em código de produção — os testes é que mentiam. |
| `vision/` — núcleo (captura, deteccao, reconhecimento_facial, rastreamento, gestos) | 🟡 | YOLO/reconhecimento facial funcionando com a DV20 (câmera índice corrigida em `a19bd53`). Roda só no Notebook (`numpy`/`cv2` ausentes no Pi, corretamente pulado lá). Sem teste de campo prolongado documentado. |
| `vision/pan_tilt.py` + câmera do pan/tilt | 🔴 | **Câmera do pan/tilt não transmite** (pendente desde 26/07, sem causa identificada ainda). O sinal do servo foi corrigido no firmware (`04ec281`) mas **"falta validar no hardware"** — texto literal do journal. |
| `voice/` (captura, VAD, transcrição, síntese) | 🟠 | Responde e fala quando ouve algo — mas a detecção da palavra de ativação **"Fofão" tem falha real e documentada**: variantes do Whisper (`'FO FÃO'` com espaço) escapam do regex/detector fuzzy. Decisão consciente do usuário de não mexer às pressas (24/07, ainda sem correção). Falha de microfone mudo (índice errado) ocorreu 3x, última corrigida 27/07 à noite. |
| `display/avatar_server.py` | 🟡 | Puro repasse de eventos (SSE), testado (`test_avatar_server.py`). Legenda de fala adicionada 27/07 à noite, sem validação com o avatar rodando na tela de verdade ainda. |
| `diagnostics/sensores_termicos.py` | 🟠 | **Zero teste dedicado** (nenhum `test_*` cobre este arquivo) — achado desta revisão. Lê `/sys/class/thermal` diretamente; simples, mas sem rede de segurança de teste. |

---

## Motion Core (Raspberry Pi) — `motion_core/`

| Módulo | Categoria | Evidência |
|---|---|---|
| `behavior/` — maestro (`behavior_core.py`) e comportamentos base (Repouso, Vigilância, Atender, Vigília) | 🟢 | Arbitragem por prioridade validada com testes de preempção cruzada (`test_obstaculo_vence_atender`, etc.) e em produção (log real: 423 ciclos de obstáculo/liberado processados corretamente pela `VigilanciaObstaculo`). |
| `behavior/comportamentos.py` — `IaEstrategica`, `Mentor` (IA discricionária) | 🟡 | Testado com fakes de IA; nunca visto decidindo em produção com o robô se movendo (o robô **nunca se moveu** ainda, ver `navigation/`). |
| `behavior/comportamentos.py` — `ControleManual` (novo, 27/07) | 🟡 | Implementado nesta sessão, 439 testes cobrindo arbitragem (vence Atender, perde para Segurança, timeout de inatividade). **Zero teste de hardware** — é a peça central do conserto do joystick, ainda não validada com o robô de verdade. |
| `navigation/navigation_core.py` — HOLD, MANUAL, GOTO, PATROL | 🟡 | Testados a fundo contra `FakeTransporte` (auto-ACK), nunca contra o Mega físico em movimento. Vocabulário MANUAL ampliado nesta sessão (`MOVE_CONTINUOUS`/`MOVE_DISTANCE`/`FAN_ON`/`FAN_OFF`). |
| `navigation/navigation_core.py` — FOLLOW, EXPLORE | 🟠 | **Escopo mínimo deliberado**, documentado no próprio módulo: "dependem de dados que só existem com o Vision Core rodando de verdade" (FOLLOW) ou de um algoritmo de exploração mais elaborado (EXPLORE). |
| `navigation/fusao_sensores.py` | 🟠 | Primeira versão por odometria pura (malha aberta — conta o que mandou, não o que a roda fez), documentado no próprio módulo. Sem encoder físico instalado (decisão do usuário, 27/07: não usar o LM12-3004NA). |
| `memory/` (database, api, bridge, manutencao, replica, vault) | 🟢 | `asyncio.Lock` + modo WAL resolvem o achado de concorrência da vistoria de 24/07. Backup/réplica testados; a réplica cruzada Pi↔Notebook está documentada e testada, com a ressalva operacional conhecida (backup agendado às 3h, Pi nem sempre ligado nesse horário — não é bug de código). |
| `webui/server.py` — dashboard, mapa, diagnóstico, configuração | 🟢 | Páginas servidas e testadas; regra "nunca mostrar valor inventado como medida" aplicada de forma consistente (`sem_eco`, rumo em vermelho sem calibração). |
| `webui/server.py` — `/comando` (joystick) | 🟡 | **Corrigido nesta sessão** (antes pulava o `NavigationCore` inteiro, sem teste nenhum). Agora com 6 testes novos e passando pelo maestro corretamente. **Testes no hardware pendentes** — é o item 1 do fluxo de 7 etapas ainda em aberto. |

---

## Hardware Core (Arduino Mega) — `firmware/hardware_core/`

| Módulo | Categoria | Evidência |
|---|---|---|
| `safety_manager.h` | 🟢 | Validado ao vivo na bancada (24/07): ponto cego do SCAN_FRONT fechado, `SAFE_MODE` deixou de ser estado sem saída (26/07), watchdog de hardware (`wdt_enable`) ativo. Camada mais crítica do projeto e a mais testada fisicamente. |
| `motor_manager.h` — geração de passos | 🟢 | `ENABLE_ATIVO_EM_BAIXO` calibrado fisicamente (18/07). Núcleo estável. |
| `motor_manager.h` — carência do relé (novo, 27/07) | 🟡 | Corrigido nesta sessão (achado do usuário), compilado (RAM 46,3%), **não gravado no Mega ainda**. |
| `radar_manager.h` | 🟢 | Offset físico calibrado ao vivo com o usuário (`RADAR_OFFSET_GRAUS = -45`), arco de 120° confirmado suficiente na prática. |
| `sensor_ultrassonico.h` — leitura frontal/traseira | 🟡 | Bug do `sensor_ok` corrigido e validado ao vivo (27/07, mão na frente do sensor). **A correção do descarte por cegueira do loop (nesta sessão) ainda não foi gravada nem medida** — os contadores `frontal_descartes`/`traseiro_descartes` são a validação pendente. |
| `bussola_manager.h` (QMC6310) | 🟠 | Conectada e lendo (`bussola_conectada: true`), mas **`rumo_valido: false`** — ainda não calibrada com o robô montado. Corretamente recusa afirmar rumo antes disso. |
| `imu_manager.h` (MPU6050) | 🟢 | Em uso na cadeia de segurança (inclinação crítica, impacto) desde a Fase 4, validado na vistoria de 24/07. |
| `command_executor.h` — `SET_PAN_TILT` | 🟡 | Sinal invertido nos dois eixos corrigido (`04ec281`, 26/07). **"Falta validar no hardware"** — texto literal do journal, ainda de pé. |
| `telemetry_manager.h` | 🟢 | Regime duplo (parado/andando) implementado e com raciocínio de custo medido (envio bloqueia 74ms→68ms). O regime de "andando" em si nunca foi testado com o robô se movendo — mas o manager como transporte de dados está maduro. |
| `encoder_manager.h` | 🔴 *(fora de escopo por decisão)* | Pinos 18/19 reservados, sem hardware físico ligado. Decisão explícita do usuário (27/07): **não usar** o encoder indutivo LM12-3004NA. Não é uma pendência a resolver — é escopo cortado conscientemente. Mantido aqui por transparência, não por expectativa de correção. |
| `dht_manager.h`, `bateria_manager.h` | 🟡 | Implementados, aparecem na telemetria condicionalmente (`dht_valido`, `bateria_lida`), sem relato de validação física recente no journal. |

---

## Leitura do mapa

**Padrão que se repete:** boa parte do projeto está em 🟡 pelo mesmo motivo —
código correto, testado no nível que dá para testar sem o robô andando, e
**o robô nunca se moveu de verdade ainda**. Isso não é um problema espalhado
em vários módulos; é **um único gargalo** (testes no hardware, etapa 5 do
fluxo) que, uma vez destravado, deve mover vários 🟡 para 🟢 de uma vez.

**Os três 🔴 reais** (câmera do pan/tilt, wake word, sensor_termicos sem
teste) são independentes entre si e não bloqueiam o robô andar.

**Próximo passo natural:** gravar o firmware desta sessão no Mega e testar o
joystick — é o que resolve o gargalo do parágrafo acima.
