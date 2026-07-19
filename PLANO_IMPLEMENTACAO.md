# ORION OS — Plano de Implementação

Cada fase tem entregáveis e critérios de conclusão. **Não avance sem os
testes da fase passando.** Os capítulos citados estão em `docs/ses/`.

---

## Fase 0 — Esqueleto do projeto
- [x] `pyproject.toml` com dependências e extras `[dev]`.
- [x] Pacote `src/orion/` importável; `python -m orion` imprime versão e sai.
- [x] `ruff` e `pytest` configurados; CI local via `tools/check.sh`.
- **Pronto quando:** `pip install -e ".[dev]" && pytest` roda sem erros.

## Fase 1 — Kernel (Cap 6)
- [x] Configuration Manager: carrega e valida `config/orion.yaml` (Cap 17).
- [x] Logger estruturado (JSON em arquivo + console legível).
- [x] Event Bus assíncrono: publish/subscribe por tópico, prioridades.
- [x] Service Registry: registro, estados (STARTING/RUNNING/DEGRADED/STOPPED).
- [x] Health Monitor + Watchdog: heartbeats, escalonamento de recuperação.
- [x] Boot Manager: sequência do Cap 6 seção 4, tolerando módulos ausentes.
- **Pronto quando:** boot em modo `--sim` chega a `system.ready`;
  testes unitários do bus, registry e watchdog passando.

## Fase 2 — Comunicação + firmware mínimo (Caps 5, 14, 10)
- [x] Camada de transporte com duas variantes: `TcpTransport`
      (Notebook↔Raspberry, Ethernet) e `SerialTransport` (Raspberry↔Arduino).
- [x] Camada de enquadramento: delimitação, escape, CRC16 (serial) e
      framing de mensagens no stream TCP.
- [x] Mensagens: COMMAND/ACK/NACK/EVENT/TELEMETRY/RESPONSE/HEARTBEAT —
      mesmo formato JSON nos dois enlaces.
- [x] APIs `comm.send / comm.publish / comm.request` com retransmissão e
      roteamento Notebook→Raspberry→Arduino pelo campo destino.
- [x] Descoberta: conexão TCP ao Raspberry + WHO_ARE_YOU ao Arduino;
      verificação de versão de protocolo.
- [x] Simuladores: `tools/sim_raspberry.py` (TCP) e `tools/sim_arduino.py`
      (porta serial virtual).
- [x] Firmware mínimo no Mega: responde WHO_ARE_YOU, heartbeat, RETURN_STATUS.
- **Pronto quando:** notebook conversa com o Raspberry (real e simulado) e,
  através dele, com o Mega (real e simulado); perda de heartbeat em qualquer
  enlace gera `comm.module_lost` e reconexão automática.

## Fase 3 — Banco de dados e memória no Raspberry (Caps 15, 11)
- [x] Database Manager no Raspberry: SQLite WAL no SSD, migrações
      versionadas, integrity_check.
- [x] Todas as tabelas e índices do Cap 15.
- [x] API de memória: remember/recall/update/forget/context/stats,
      exposta ao Notebook via comm.request (Ethernet).
- [x] Backup diário no SSD + réplica cruzada no Notebook; restauração;
      tarefa de retenção.
- **Pronto quando:** testes de migração, backup, réplica e recuperação
  passando; `memory.context()` chamado do Notebook responde em < 100 ms
  com massa de teste.

## Fase 4 — Hardware Core completo (Cap 10)
- [x] Firmware modular: Motor/Sensor/Radar/IMU/Encoder/Command/Telemetry/Safety.
- [x] Máquina de estados completa (BOOT→...→SAFE_MODE).
- [x] Comandos: MOVE_FORWARD, MOVE_DISTANCE, MOVE_CONTINUOUS,
      TURN_LEFT/RIGHT, STOP, DOCK, SCAN_FRONT, LIGHT_ON/OFF,
      RETURN_STATUS — com ACK e progresso.
- [x] Pacote periódico Radar Inteligente (ultrassons, MPU, motores,
      velocidade estimada).
- [x] Segurança reativa: parada por distância mínima, inclinação, timeout.
- [x] Telemetria periódica de todos os sensores.
- **Pronto quando:** bancada executa cada comando com rodas suspensas;
  obstáculo à frente para o robô sem participação do Raspberry nem do
  notebook.
  **[~] DESBLOQUEADO em 2026-07-19 — bancada parcial feita.** Hardware
  montado e validado: os dois ultrassons medindo (22/23 e 26/27, após
  corrigir fios deslocados no conector), motores girando (TB6600, após
  ligar PUL−/DIR− ao GND do Mega), segurança reativa disparando sozinha
  (OBSTACLE_DETECTED com obstáculo real). IMU/encoders/LED seguem não
  conectados. Falta a bancada formal completa: cada comando de movimento
  com rodas suspensas + confirmação do micropasso das chaves DIP (contar
  voltas) para calibrar PASSOS_POR_METRO.

## Fase 5 — Vision Core no Notebook (Cap 8)
- [x] Pipeline no Notebook: captura → YOLO → reconhecimento facial →
      rastreamento → eventos no Event Bus local.
- [x] Cálculo Pan/Tilt com limites de velocidade; comandos enviados via
      Raspberry ao Arduino.
- [x] Recuperação de câmera desconectada (modo SEM_VISÃO).
- **Pronto quando:** pessoa na frente da câmera gera `vision.person_detected`
  no Event Bus e o Pan/Tilt a mantém centralizada.
  **[ ] AINDA NÃO — parcialmente bloqueado por hardware.** Pipeline
  completo validado com a webcam real (captura, YOLO, reconhecimento
  facial, rastreamento, Event Bus, recuperação) — sem erros, mas sem
  pessoa no quadro no teste feito em 2026-07-17. O comando SET_PAN_TILT foi
  testado no Mega real (ACK ok), mas os servos pan/tilt ainda não estão
  montados fisicamente (mesma pendência da Fase 4) — falta isso e um teste
  com pessoa em frente à câmera para fechar o critério por completo.

## Fase 6 — Voz e IA (Caps 9, 7)
- [x] Wake word "Fofão" (openWakeWord ou similar) + Whisper + Piper, offline.
- [x] Estados IDLE→LISTENING→...→SPEAKING com eventos voice.*.
- [x] AI Manager: Ollama com prompt de sistema, contexto vindo da memória.
- [x] Mission Planner: fluxo de decisão do Cap 7 seção 4.
- **Pronto quando:** "Fofão, que horas são?" recebe resposta falada;
  "Fofão, acenda a lanterna" resulta em LIGHT_ON no Mega.
  **[~] QUASE — integração completa validada em 2026-07-19.** O serviço de
  voz ao vivo (conversar_fofao.py) agora conecta no Motion Core via TCP e
  os comandos passam primeiro pelo Mission Planner (Cap 7 s.4): "acenda a
  lanterna" → LIGHT_ON **ACKado pelo Mega real** através da cadeia
  TCP+serial (validado por teste de texto ponta a ponta); hora → resposta
  direta; conversa livre → gemma3; interações registradas no banco do SSD.
  Dois bugs de regex corrigidos com regressão ("apague" no subjuntivo;
  "desligue a luz" ligava a lanterna). Escuta 20x mais barata com o portão
  VAD. Falta só a validação FALADA do critério — o mic atual é fraco;
  mic USB + caixinhas chegam qui 23/07, missão de áudio sex 24/07.

## Fase 7 — Motion Core / Navegação no Raspberry (Cap 12)
- [ ] Serviço `motion_core` no Raspberry: recebe missões via TCP,
      comanda o Arduino via serial.
- [ ] Fusão de sensores: odometria (encoders) + correção por IMU;
      posição estimada publicada como motion.position.
- [ ] Modos PATROL, FOLLOW, GOTO, MANUAL, HOLD.
- [ ] Desvio de obstáculos em 3 camadas (Cap 18).
- [ ] Autocalibração da primeira inicialização: deslocamento controlado
      + medição pela visão + fator de correção salvo na configuração
      (Cap 12, seção 9).
- **Pronto quando:** patrulha em rota de teste com desvio funcional;
  FOLLOW mantém distância de pessoa autorizada; fator de calibração
  reduz o erro de deslocamento medido.

## Fase 8 — Avatar + interface web (Cap 13)
- [ ] Avatar em tela cheia no notebook, reagindo aos estados de voz e
      alertas; multimídia no notebook.
- [ ] Interface web servida pelo Raspberry: DASHBOARD/CONVERSA/MAPA/
      DIAGNÓSTICO/CONFIGURAÇÃO, acessível pelo IP local.
- [ ] Mapa polar do radar em tempo real.
- [ ] Acesso remoto opcional via Raspberry Pi Connect documentado
      (nunca requisito).
- **Pronto quando:** interface reflete eventos em < 500 ms acessada do
  celular na rede local.

## Fase 9 — Diagnóstico e segurança (Caps 16, 18)
- [ ] Autotestes de boot completos; comando "Fofão, autoteste".
- [ ] Coleta de métricas (CPU/RAM/temperatura) com limiares e ações.
- [ ] Modos degradados: SEM_VISÃO, SEM_VOZ, SEM_MOTION, SEM_IA.
- [ ] "Fofão, pare" com prioridade máxima ponta a ponta.
- **Pronto quando:** INT-05 e INT-06 (Cap 19) passam em hardware real.

## Fase 10 — Validação 1.0 (Cap 19)
- [ ] Todos os cenários INT-01..09 executados e registrados.
- [ ] Testes de campo CAMPO-01..06.
- [ ] Critérios de aprovação do Cap 19 seção 6 atendidos.
- **Pronto quando:** ORION OS 1.0 aprovado conforme especificação.
