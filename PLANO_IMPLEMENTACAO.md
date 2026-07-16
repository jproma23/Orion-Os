# ORION OS — Plano de Implementação

Cada fase tem entregáveis e critérios de conclusão. **Não avance sem os
testes da fase passando.** Os capítulos citados estão em `docs/ses/`.

---

## Fase 0 — Esqueleto do projeto
- [ ] `pyproject.toml` com dependências e extras `[dev]`.
- [ ] Pacote `src/orion/` importável; `python -m orion` imprime versão e sai.
- [ ] `ruff` e `pytest` configurados; CI local via `tools/check.sh`.
- **Pronto quando:** `pip install -e ".[dev]" && pytest` roda sem erros.

## Fase 1 — Kernel (Cap 6)
- [ ] Configuration Manager: carrega e valida `config/orion.yaml` (Cap 17).
- [ ] Logger estruturado (JSON em arquivo + console legível).
- [ ] Event Bus assíncrono: publish/subscribe por tópico, prioridades.
- [ ] Service Registry: registro, estados (STARTING/RUNNING/DEGRADED/STOPPED).
- [ ] Health Monitor + Watchdog: heartbeats, escalonamento de recuperação.
- [ ] Boot Manager: sequência do Cap 6 seção 4, tolerando módulos ausentes.
- **Pronto quando:** boot em modo `--sim` chega a `system.ready`;
  testes unitários do bus, registry e watchdog passando.

## Fase 2 — Comunicação + firmware mínimo (Caps 5, 14, 10)
- [ ] Camada de transporte com duas variantes: `TcpTransport`
      (Notebook↔Raspberry, Ethernet) e `SerialTransport` (Raspberry↔Arduino).
- [ ] Camada de enquadramento: delimitação, escape, CRC16 (serial) e
      framing de mensagens no stream TCP.
- [ ] Mensagens: COMMAND/ACK/NACK/EVENT/TELEMETRY/RESPONSE/HEARTBEAT —
      mesmo formato JSON nos dois enlaces.
- [ ] APIs `comm.send / comm.publish / comm.request` com retransmissão e
      roteamento Notebook→Raspberry→Arduino pelo campo destino.
- [ ] Descoberta: conexão TCP ao Raspberry + WHO_ARE_YOU ao Arduino;
      verificação de versão de protocolo.
- [ ] Simuladores: `tools/sim_raspberry.py` (TCP) e `tools/sim_arduino.py`
      (porta serial virtual).
- [ ] Firmware mínimo no Mega: responde WHO_ARE_YOU, heartbeat, RETURN_STATUS.
- **Pronto quando:** notebook conversa com o Raspberry (real e simulado) e,
  através dele, com o Mega (real e simulado); perda de heartbeat em qualquer
  enlace gera `comm.module_lost` e reconexão automática.

## Fase 3 — Banco de dados e memória no Raspberry (Caps 15, 11)
- [ ] Database Manager no Raspberry: SQLite WAL no SSD, migrações
      versionadas, integrity_check.
- [ ] Todas as tabelas e índices do Cap 15.
- [ ] API de memória: remember/recall/update/forget/context/stats,
      exposta ao Notebook via comm.request (Ethernet).
- [ ] Backup diário no SSD + réplica cruzada no Notebook; restauração;
      tarefa de retenção.
- **Pronto quando:** testes de migração, backup, réplica e recuperação
  passando; `memory.context()` chamado do Notebook responde em < 100 ms
  com massa de teste.

## Fase 4 — Hardware Core completo (Cap 10)
- [ ] Firmware modular: Motor/Sensor/Radar/IMU/Encoder/Command/Telemetry/Safety.
- [ ] Máquina de estados completa (BOOT→...→SAFE_MODE).
- [ ] Comandos: MOVE_FORWARD, MOVE_DISTANCE, MOVE_CONTINUOUS,
      TURN_LEFT/RIGHT, STOP, DOCK, SCAN_FRONT, LIGHT_ON/OFF,
      RETURN_STATUS — com ACK e progresso.
- [ ] Pacote periódico Radar Inteligente (ultrassons, MPU, motores,
      velocidade estimada).
- [ ] Segurança reativa: parada por distância mínima, inclinação, timeout.
- [ ] Telemetria periódica de todos os sensores.
- **Pronto quando:** bancada executa cada comando com rodas suspensas;
  obstáculo à frente para o robô sem participação do Raspberry nem do
  notebook.

## Fase 5 — Vision Core no Notebook (Cap 8)
- [ ] Pipeline no Notebook: captura → YOLO → reconhecimento facial →
      rastreamento → eventos no Event Bus local.
- [ ] Cálculo Pan/Tilt com limites de velocidade; comandos enviados via
      Raspberry ao Arduino.
- [ ] Recuperação de câmera desconectada (modo SEM_VISÃO).
- **Pronto quando:** pessoa na frente da câmera gera `vision.person_detected`
  no Event Bus e o Pan/Tilt a mantém centralizada.

## Fase 6 — Voz e IA (Caps 9, 7)
- [ ] Wake word "Fofão" (openWakeWord ou similar) + Whisper + Piper, offline.
- [ ] Estados IDLE→LISTENING→...→SPEAKING com eventos voice.*.
- [ ] AI Manager: Ollama com prompt de sistema, contexto vindo da memória.
- [ ] Mission Planner: fluxo de decisão do Cap 7 seção 4.
- **Pronto quando:** "Fofão, que horas são?" recebe resposta falada;
  "Fofão, acenda a lanterna" resulta em LIGHT_ON no Mega.

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
