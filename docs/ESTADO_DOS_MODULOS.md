# ORION OS — Mapa de saúde dos módulos

> ## AVISO: ESTE MAPA ESTA VENCIDO (2026-07-30)
>
> Ele descreve 2026-07-27 e **não foi reconferido desde então**. Pela regra de
> rebaixamento 1 da própria rubrica ("qualquer commit que toca o módulo
> invalida o status anterior"), toda linha abaixo vale `?` até nova
> conferência: houve ~30 commits depois desta data, em navegação, maestro,
> webui e firmware.
>
> O que já se sabe estar errado aqui, sem precisar reconferir nada:
>
> - **"Mission Core (Notebook)"** — a divisão de máquinas mudou duas vezes
>   depois deste mapa. A vigente é a **EDR-0036** (30/09/2026): Mission Core no
>   Pi, visão e voz no Notebook. Em 25/10 ela estava executada pela metade.
> - **"O robô ainda não se moveu"** (topo e leitura do mapa) — falso desde
>   2026-07-29. Ver o teto de política no `ARQUITETURA.txt`.
> - **`vision/pan_tilt.py` 🔴 "câmera não transmite"** — resolvido em 28/07; o
>   pan/tilt inclusive SEGUE pessoa hoje.
> - **`encoder_manager.h` 🔴 "fora de escopo por decisão"** — a decisão foi
>   revertida; o encoder de roda é pendência ativa e bloqueia a odometria.
> - **`navigation` EXPLORE 🟠 "algoritmo ainda não escrito"** — foi escrito em
>   30/07 e roda em produção (4 segmentos de 30 cm por decisão).
> - **As contagens de "439 testes"** — hoje são 608 no Notebook e 538 no Pi.
>
> Não reclassifiquei as linhas de propósito: a rubrica exige evidência datada
> por módulo, e inventar cor sem medir é exatamente o que ela existe para
> impedir. Marcar como vencido é a leitura honesta até alguém rodar o
> procedimento de marcação.

**Snapshot de:** 2026-07-27
**Regra de classificação:** Rubrica do Mapa de Saúde, em `ARQUITETURA.txt`.
**Reconferido:** todas as linhas abaixo foram checadas no código nesta sessão
(não copiadas do journal nem de uma versão anterior deste mapa).

## Teto de política, aplicado

**Nada abaixo é 🟢.** O robô ainda não se moveu — esse é o gate que falta
para qualquer módulo, por definição da rubrica.

Isso cria uma tensão que vale registrar em vez de esconder: **três módulos
têm validação de hardware real e datada** que, isoladamente, satisfaria a
condição técnica de 🟢 — `communication/` (reconexão TCP no Pi real, 25/07),
`safety_manager.h` (obstáculo real testado com a mão, 27/07) e
`radar_manager.h`/`motor_manager.h` (calibração física ao vivo, 18–24/07).
Mantidos em 🟡 mesmo assim, pelo teto de política explícito, não porque a
evidência citada seja fraca — está marcada em cada linha, com data.

**O firmware C++ não tem suíte de teste automatizada.** Confirmado nesta
sessão: `find firmware -iname "*test*"` não retorna nada. O harness de
validação cruzada CRC16 (Python↔C++) citado no journal de 17/07 foi um
script `g++` avulso, não preservado no repositório — não é evidência
reproduzível hoje (regra de rebaixamento 3). Por isso, nas tabelas de
Hardware Core, "Evidência" nunca cita "suíte de N testes" — cita compilação
(`pio run`) mais validação de bancada com data, ou a ausência de qualquer
uma das duas.

---

## Mission Core (Notebook) — `src/orion/`

| Módulo | Status | Últ. verificação | Evidência | Gate faltante |
|---|---|---|---|---|
| `kernel/` (event_bus, registry, watchdog, boot, config) | 🟡 | 2026-07-27 | 439 testes (Notebook). `python -m orion --sim` chega a `system.ready` — mas `--sim` é simulado, não é boot com Arduino/Pi reais. | boot real (sem `--sim`) documentado com data |
| `communication/` (crc, framing, transport, service, discovery, heartbeat) | 🟡 | 2026-07-27 | 441 testes. Hardware: reconexão TCP validada no Pi real (25/07); **hoje** achado e corrigido um bug real de desligamento (`SerialTransport.fechar()` preso atrás de um `read()` bloqueante no mesmo executor de 1 thread — corrigido com `cancel_read()`, mais reordenação em `ComunicacaoService.encerrar()`). Medido: parte da queda de 91s→1s no restart, junto com a linha da webui abaixo. | nenhum tecnicamente — mantido 🟡 pelo teto de política (ver acima) |
| **NOVO** `WebUIServer.encerrar()` / `_handler_sse` — desligamento gracioso | 🟡 | 2026-07-27 | Bug real e **causa dominante** de um travamento de ~90s em `systemctl restart` (achado nesta sessão, não conhecido antes): `AppRunner.cleanup()` espera handlers SSE em andamento terminarem sozinhos; com o dashboard aberto numa aba do navegador, isso nunca acontecia sozinho. Corrigido com `asyncio.Event` que o loop do SSE escuta. **Medido em produção com cliente SSE real conectado**: `systemctl restart` de 91s para 1s, confirmado no log ponta a ponta até `Motion Core encerrado`. Nota honesta: o teste unitário (`TestClient`/`TestServer`) NÃO discrimina este bug — só o teste contra o serviço real provou. | nenhum tecnicamente — mantido 🟡 pelo teto de política; teste unitário existente não é confiável para este cenário específico |
| `mission/` (ai_manager, mission_planner, grounding, diario) | 🟡 | 2026-07-27 | 439 testes. Campo: log de produção mostra OpenRouter respondendo 200 OK no Notebook real (data exata não registrada no log lido). | validação de campo com data explícita |
| `vision/` — núcleo (captura, deteccao, reconhecimento_facial, rastreamento, gestos) | 🟡 | 2026-07-27 | 439 testes no Notebook (roda com `numpy`/`cv2`; corretamente pulado no Pi — `test_vision_*` nos 7 skips). Campo: YOLO/DV20 funcionando após correção do índice de câmera (`a19bd53`, sem data exata de validação registrada). | validação de campo com data explícita |
| `vision/pan_tilt.py` + câmera física do pan/tilt | 🔴 | 2026-07-27 | Câmera não transmite — pendência aberta desde 26/07, sem causa identificada. Correção de sinal do servo (`04ec281`) existe no código mas está marcada no journal como "falta validar no hardware". | diagnosticar por que a câmera não transmite |
| `voice/` — captura, VAD, transcrição, síntese | 🟡 | 2026-07-27 | Testes passam (`test_voice_*`, roda completo no Notebook). Falha de microfone mudo (índice errado) corrigida 27/07 à noite, sem validação de campo registrada após a correção. | validação de campo pós-correção do índice |
| `voice/wake_word.py` — detecção de "Fofão" | 🟠 | 2026-07-27 | 10 testes unitários passam (`test_voice_wake_word.py`), mas **não cobrem a falha real**: transcrições reais do Whisper (`'FO FÃO'` com espaço, `'Fulfo'`, vazio) escapam do detector, testado ao vivo repetidas vezes em 24–25/07. Decisão consciente do usuário de não corrigir às pressas. | corrigir a normalização/tolerância fonética; teste que reproduza os casos reais |
| `display/avatar_server.py` | 🟡 | 2026-07-27 | 439 testes (`test_avatar_server.py`). Legenda de fala adicionada 27/07 à noite, sem validação com o avatar rodando na tela física depois disso. | validação visual na tela do avatar |
| `diagnostics/sensores_termicos.py` | 🟠 | 2026-07-27 | **Nenhum arquivo de teste referencia este módulo** — confirmado por busca nesta sessão (nenhum `test_diagnostics*`/`test_sensores*` existe). Nunca mencionado no journal como validado. | escrever teste; validar leitura em `/sys/class/thermal` real |

---

## Motion Core (Raspberry Pi) — `motion_core/`

| Módulo | Status | Últ. verificação | Evidência | Gate faltante |
|---|---|---|---|---|
| `behavior/behavior_core.py` (maestro — arbitragem) | 🟡 | 2026-07-27 | 439 testes cobrindo preempção cruzada. Campo: 423 ciclos reais de obstáculo/liberado processados corretamente no log do Pi (27/07) — validação de hardware genuína da arbitragem, mas mantida 🟡 pelo teto. | nenhum tecnicamente — mantido 🟡 pelo teto de política |
| `behavior/comportamentos.py` — `IaEstrategica`, `Mentor` | 🟡 | 2026-07-27 | Testes com fakes de IA passam. Nunca visto decidindo com o robô se movendo de verdade. | validação com movimento real |
| `behavior/comportamentos.py` — `ControleManual` (novo) | 🟡 | 2026-07-27 | Implementado e testado nesta sessão (6 testes novos: assume, timeout, ignora não-manual, perde para segurança, vence Atender). **Zero validação de hardware** — commit `6b8815e`, mesmo dia. | testar o joystick com o robô ligado |
| `navigation/navigation_core.py` — HOLD, MANUAL, GOTO, PATROL | 🟡 | 2026-07-27 | 21 testes contra `FakeTransporte` com auto-ACK (nunca contra o Mega físico em movimento). Vocabulário MANUAL ampliado nesta sessão, commit `6b8815e`. | movimento real do robô |
| `navigation/navigation_core.py` — FOLLOW, EXPLORE | 🟠 | 2026-07-27 | Escopo mínimo **deliberado**, documentado no cabeçalho do módulo: dependem do Vision Core ponta-a-ponta (FOLLOW) ou de algoritmo de exploração ainda não escrito (EXPLORE). | integração real com Vision Core; algoritmo de EXPLORE |
| `navigation/fusao_sensores.py` | 🟠 | 2026-07-27 | "Primeira versão" por odometria pura, malha aberta (conta o que mandou, não o que a roda fez) — documentado no próprio módulo. Sem encoder físico (decisão do usuário, 27/07, de não instalar o LM12-3004NA). | validação externa (ultrassom+bússola) como checagem de realidade |
| `memory/` (database, api, bridge, manutencao, replica, vault) | 🟡 | 2026-07-27 | 439 testes. `asyncio.Lock` + WAL resolvem o achado de concorrência de 24/07 — mas sem teste de carga real (webui consultando + backup rodando ao mesmo tempo, no Pi de verdade) depois da correção. | teste de concorrência real no Pi |
| `webui/server.py` — dashboard, mapa, diagnóstico, configuração | 🟡 | 2026-07-27 | 439 testes. Campo: páginas (rosa dos ventos, radar 360°) conferidas visualmente pelo usuário em 27/07 (mudanças pedidas por ele mesmo após ver o resultado). | validação formal com data registrada |
| `webui/server.py` — `/comando` (joystick) | 🟡 | 2026-07-27 | Corrigido nesta sessão: antes pulava o `NavigationCore` inteiro e não tinha teste nenhum. 6 testes novos, commit `6b8815e`. **Validado em produção real** (novo `tools/testar_joystick.py`): LIGHT_ON/LIGHT_OFF/STOP confirmados ponta a ponta contra o Mega de verdade — log real mostra `'controle_manual' assume (prio 95)`. Giro real (motor) ainda não testado, por decisão consciente (sem confirmação visual do robô em posição segura). | testar `--com-motor` com o robô em posição segura e alguém observando |

---

## Hardware Core (Arduino Mega) — `firmware/hardware_core/`

*(Sem suíte automatizada — ver nota no topo. "Evidência" cita compilação + bancada.)*

| Módulo | Status | Últ. verificação | Evidência | Gate faltante |
|---|---|---|---|---|
| `safety_manager.h` | 🟡 | 2026-07-27 | Compila (`pio run`, sucesso). Bancada: ponto cego do SCAN_FRONT fechado e SAFE_MODE deixou de ser estado sem saída (24–26/07); obstáculo real testado com a mão em 27/07 (174→61→172cm). | nenhum tecnicamente — mantido 🟡 pelo teto de política |
| `motor_manager.h` — geração de passos (núcleo) | 🟡 | 2026-07-27 | Compila. `ENABLE_ATIVO_EM_BAIXO` calibrado fisicamente em 18/07. **Arquivo alterado nesta sessão** (carência do relé) — regra de rebaixamento 1: qualquer commit que toca o módulo invalida validação anterior até nova conferência. | gravar no Mega e revalidar a geração de passos |
| `motor_manager.h` — carência do relé (novo, 27/07) | 🟡 | 2026-07-27 | Compila (RAM 46,3%). Achado do usuário, corrigido nesta sessão, commit `6b8815e`. **Não gravado no Mega.** | gravar e medir com 1000 passos contra parede |
| `radar_manager.h` | 🟡 | 2026-07-27 | Compila. Offset físico (`RADAR_OFFSET_GRAUS = -45`) e arco de 120° calibrados ao vivo (24/07). **Arquivo alterado nesta sessão** (getter `frontalDescartes()`, não toca a calibração) — mesmo assim invalidado pela regra 1 até nova conferência. | gravar no Mega e revalidar |
| `sensor_ultrassonico.h` — leitura frontal/traseira (núcleo) | 🟡 | 2026-07-27 | Compila. Bug do `sensor_ok` corrigido e validado ao vivo em 27/07 (mão na frente do sensor, 174→61→172cm). **Arquivo alterado nesta sessão** — invalidado pela regra 1. | gravar e revalidar |
| `sensor_ultrassonico.h` — descarte por cegueira do loop (novo, 27/07) | 🟡 | 2026-07-27 | **Gravado no Mega e medido**: `frontal_descartes=29`, `traseiro_descartes=10` acumulados em poucos minutos de uptime real — confirma a hipótese (o envio da telemetria cega o loop durante o eco com frequência real, não só teórica). Ainda não correlacionado, em janela longa, com sumiço dos 423 ciclos de obstáculo falso. | observação prolongada correlacionando descartes com ausência de obstáculo falso |
| `bussola_manager.h` (QMC6310) | 🟠 | 2026-07-27 | Compila. Conectada e lendo (`bussola_conectada: true`), mas `rumo_valido: false` — recusa afirmar rumo por falta de calibração, corretamente. Isto é funcionamento parcial por natureza (falta um passo), não risco. | calibrar com o robô montado (`CALIBRATE_COMPASS`) |
| `imu_manager.h` (MPU6050) | ❌ removido | 2026-08-18 | **Arquivo deletado** com a IMU descartada pelo dono (decisão de 16/08, executada em 18/08). Segurança de inclinação/impacto saiu do firmware; restam a parada reativa por obstáculo e o timeout de comando. Sem validação pendente — módulo não existe mais. | — |
| `command_executor.h` — `SET_PAN_TILT` | 🟡 | 2026-07-27 | Compila. Sinal invertido nos dois eixos corrigido em `04ec281` (26/07). Journal registra literalmente "falta validar no hardware" — ainda de pé. | validar fisicamente o pan/tilt |
| `telemetry_manager.h` | 🟡 | 2026-07-27 | Compila. Regime duplo (parado/andando) implementado com custo medido (envio 74→68ms). Regime "andando" nunca testado com o robô em movimento. **Arquivo alterado nesta sessão** (campos de descarte). | testar telemetria reduzida com movimento real |
| `bussola_manager.h` — I2C físico | 🟡 | 2026-08-18 | Scanner de I2C no boot confirma o chip presente (27/07, após religar o fio). Achado de hard-iron dos motores documentado, sem calibração final. A IMU (MPU6050) saiu do barramento em 18/08 — a bússola é o único sensor I2C além dos servos da cabeça. | ver linha da bússola acima |
| `encoder_manager.h` | 🔴 *(fora de escopo por decisão)* | 2026-07-27 | Pinos 18/19 reservados, sem sensor físico ligado. Decisão explícita do usuário (27/07): não usar o encoder indutivo LM12-3004NA. Não é bug — é escopo cortado conscientemente. | nenhum — reabrir só se a decisão for revertida |
| `dht_manager.h`, `bateria_manager.h` | 🟠 | 2026-07-27 | Compilam, aparecem condicionalmente na telemetria (`dht_valido`, `bateria_lida`). Nenhuma validação de bancada com data encontrada no journal para nenhum dos dois. | validar leitura real e registrar data |

---

## Leitura do mapa

**Nenhuma célula é 🟢** — correto pelo teto de política: o robô não se moveu.

**Onde a evidência é mais forte que a cor sugere:** `communication/`,
`safety_manager.h` e a arbitragem do maestro têm validação
de hardware real e datada. A cor fica em 🟡 pelo teto, não porque falte
evidência — está registrada em cada linha.

**Onde a cor é genuinamente frágil (🟠/🔴):** wake word (falha real
documentada, não corrigida), câmera do pan/tilt (quebrada), FOLLOW/EXPLORE e
fusão de sensores (parciais por design), bússola (falta um passo:
calibração), `sensores_termicos.py` e `dht_manager.h`/`bateria_manager.h`
(sem validação registrada nenhuma). `encoder_manager.h` é o único 🔴 que não
é uma pendência — é escopo cortado por decisão.

**O gargalo único:** a maior parte da parede de 🟡 tem a mesma causa —
código testado no nível que dá para testar sem o robô andando. Uma sessão
de testes no hardware, seguindo o fluxo de 7 etapas, deve mover vários 🟡
para 🟢 de uma vez. É o próximo passo natural.
