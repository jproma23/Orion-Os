# ORION OS — Jornal de Recuperação

Log cronológico do desenvolvimento. Cada entrada é escrita **no momento em
que a etapa acontece**, não em resumo de fim de sessão. Objetivo: qualquer
sessão nova (minha ou do Claude) consegue ler este arquivo e saber
exatamente onde o projeto parou, o que já funciona e o que falta.

Formato de cada entrada: data, o que foi feito, estado resultante, próximo
passo.

---

## 2026-07-16

- Recebido o scaffold inicial do projeto (`orion-os-tcc.zip`) via e-mail,
  transferido do celular para o Raspberry Pi por servidor de upload
  temporário (encerrado após uso) e extraído em `~/orion-os`.
- Repositório git inicializado em `~/orion-os` (ainda sem commits).
- Decisão: desenvolvimento vai seguir `PLANO_IMPLEMENTACAO.md` fase por
  fase, sem pular etapas, sempre com os testes da fase passando antes de
  avançar (regra já definida no próprio plano).
- Estado do código: apenas scaffold. Único código funcional é
  `src/orion/__main__.py` (Fase 0, imprime versão) e o teste de fumaça
  `tests/unit/test_smoke.py`. Nenhuma fase concluída ainda.
- **Fase 0 concluída.** Criado `.venv`, `pip install -e ".[dev]"` sem erros,
  `tools/check.sh` passa limpo (ruff OK, 1 teste de fumaça OK).
  `python -m orion` e `python -m orion --sim` funcionam (só imprimem versão,
  boot real ainda não existe — é da Fase 1).
- **Próximo passo:** iniciar Fase 1 — Kernel (Cap 6): Configuration Manager
  lendo `config/orion.yaml`, Logger estruturado, Event Bus assíncrono,
  Service Registry, Health Monitor + Watchdog, Boot Manager. Ler
  `docs/ses/ORION_OS_SES_Capitulo_06_Kernel_ORION_OS.md` antes de começar.

## 2026-07-17

- **Fase 1 concluída.** Implementado em `src/orion/kernel/`:
  - `config.py` — Configuration Manager: carrega `config/orion.yaml`, valida
    esquema mínimo (tipos e obrigatoriedade dos campos usados nesta fase),
    aborta com `ErroConfiguracaoInvalida` em config inválida (Cap 17 s.2).
  - `logger.py` — Logger estruturado: console legível + arquivo JSON, nível
    via `system.log_level`.
  - `event_bus.py` — Event Bus assíncrono: publish/subscribe por tópico com
    fila de prioridades (`Prioridade.CRITICA..BAIXA`), handler com erro é
    isolado (log, não derruba o bus).
  - `registry.py` — Service Registry: registro de módulos com nome, versão,
    dependências, estado (STARTING/RUNNING/DEGRADED/STOPPED), checagem de
    dependências satisfeitas.
  - `watchdog.py` — Health Monitor (rastreia heartbeats) + Watchdog (escalona
    reconectar → reiniciar → log → publica `diagnostic.error`), cada módulo
    tratado isoladamente.
  - `boot.py` — Boot Manager: executa a sequência do Cap 6 s.4 até publicar
    `system.ready`; etapas de fases futuras (Raspberry, Arduino, banco, IA,
    Vision, Motion Core) ainda não existem e são toleradas (log +
    `diagnostic.error` informativo, sem abortar o boot).
  - `__main__.py` atualizado para rodar o boot real via `asyncio`.
- **Testes:** 32 testes unitários novos em `tests/unit/` (event bus, registry,
  watchdog, config, boot). `tools/check.sh` passa limpo (ruff + pytest).
- **Verificado manualmente:** `python -m orion --sim` chega a `system.ready`
  e encerra de forma limpa (desligamento seguro para watchdog e event bus).
- **Contexto físico confirmado:** esta sessão roda no próprio Raspberry Pi
  (Motion Core) — Mega no CH340 `/dev/ttyUSB0`. O Notebook (Mission Core)
  está ligado a ele por **USB** neste momento, não pela Ethernet que o
  EDR-0018 prevê para produção; isso cai dentro do `maintenance_usb: "auto"`
  já existente na config, não é violação de arquitetura, só o link de
  desenvolvimento atual.
- **Próximo passo:** iniciar Fase 2 — Comunicação + firmware mínimo (Caps 5,
  14, 10): camada de transporte (TCP Notebook↔Raspberry, Serial
  Raspberry↔Arduino), enquadramento com CRC16, mensagens
  COMMAND/ACK/NACK/EVENT/TELEMETRY/RESPONSE/HEARTBEAT, APIs
  comm.send/publish/request, descoberta (WHO_ARE_YOU), simuladores e
  firmware mínimo no Mega.

- **Fase 2 concluída.** Implementado em `src/orion/communication/`:
  - `crc.py` / `protocol.py` — CRC16 (CCITT-FALSE) e `Mensagem` (Cap 5 s.5):
    protocolo, origem, destino, tipo (COMMAND/ACK/NACK/EVENT/TELEMETRY/
    RESPONSE/HEARTBEAT), id, timestamp, payload, checksum, id_referencia.
  - `framing.py` — serial: STX/ETX + byte-stuffing + CRC16 (`DecodificadorSerial`
    stateful, ressincroniza após ruído, descarta quadro com CRC inválido);
    TCP: prefixo de tamanho de 4 bytes (`DecodificadorTcp`).
  - `transport.py` — `TcpTransport` (cliente), `ConexaoTcp` (lado servidor),
    `iniciar_servidor_tcp`, `SerialTransport` (pyserial via executor
    dedicado de 1 thread).
  - `service.py` — `ComunicacaoService`: comm.send (ACK + até 3
    retransmissões → `comm.link_degraded`), comm.publish (EVENT difundido +
    Event Bus local), comm.request/responder (RESPONSE correlacionada por
    id_referencia), comm.status, roteamento transparente pelo campo destino,
    CRC inválido → NACK, responde WHO_ARE_YOU automaticamente.
  - `discovery.py` — `descobrir()`: WHO_ARE_YOU + verificação de versão de
    protocolo, publica `comm.protocol_mismatch` se incompatível.
  - `heartbeat.py` — `MonitorHeartbeat`: heartbeat periódico, `comm.module_lost`
    / `comm.module_recovered`, reutiliza o `HealthMonitor` do Kernel (Fase 1).
  - `tools/sim_raspberry.py` e `tools/sim_arduino.py` — simuladores completos
    (TCP e pty) para desenvolver sem hardware.
  - `firmware/hardware_core/` — firmware mínimo em C++ (PlatformIO,
    ArduinoJson): responde WHO_ARE_YOU e RETURN_STATUS, ACKa todo COMMAND,
    envia HEARTBEAT periódico sem bloquear o loop.
- **Decisão de design:** o checksum de mensagem (campo JSON) não é validado
  no link serial (`exigir_checksum_mensagem=False` para o link com o
  Arduino) — reproduzir a serialização JSON canônica do Python em C++ seria
  frágil (especialmente floats); a integridade do link serial já é garantida
  pelo CRC16 da camada de enquadramento, que é idêntico nas duas linguagens
  (validado byte a byte, ver testes de interoperabilidade abaixo).
- **Testes:** 68 testes unitários + 3 de integração (`tests/integration/`,
  marcador `sim`) usando transportes reais (TCP de loopback, pty). Validação
  cruzada C++/Python do CRC16 e do enquadramento completo (byte-stuffing)
  feita com um pequeno harness nativo (g++) — compatibilidade confirmada nos
  dois sentidos, inclusive com bytes especiais (STX/ETX/ESC) no payload.
- **Bugs reais encontrados e corrigidos durante o desenvolvimento** (não
  eram só do teste, eram da lib):
  1. `EventBus`: se `parar()` fosse chamado antes da task de `iniciar()`
     rodar sua primeira iteração, `iniciar()` sobrescrevia `_executando` de
     volta para `True` (corrida de inicialização). Corrigido movendo o
     `True` inicial para `__init__`.
  2. `SerialTransport`: leitura e escrita concorrentes na mesma porta via o
     executor padrão (multi-thread) do asyncio corrompiam o estado interno
     do pyserial (`self.fd` virava `None` em pleno `read()`). Corrigido com
     um `ThreadPoolExecutor(max_workers=1)` dedicado por transporte,
     serializando todo acesso à porta.
  3. `SerialTransport.conectar()` não esperava o Arduino terminar de
     reiniciar (abrir a porta ativa o DTR e reseta o Mega via CH340/bootloader
     — comportamento padrão da maioria dos adaptadores USB-serial): comandos
     enviados logo após conectar se perdiam. Corrigido com `atraso_reset_s`
     (padrão 2s, configurável, 0 para portas que não resetam como um pty).
  4. `ComunicacaoService._loop_recepcao`: uma exceção de transporte (ex.:
     `ConnectionResetError` num TCP derrubado sem aviso) subia sem tratamento
     e derrubava a task de recepção por completo. Corrigido isolando por
     link (Cap 6 s.8: falha de um módulo/link não derruba o resto).
- **Validado com hardware físico real** (não só simulado):
  - Firmware compilado e gravado no Mega real (`/dev/ttyUSB0`, CH340) via
    PlatformIO; WHO_ARE_YOU, ACK, RETURN_STATUS e HEARTBEAT confirmados
    funcionando ponta a ponta com o `ComunicacaoService` real.
  - Notebook real (10.20.20.195, Debian 13) conversando pela rede Wi-Fi
    (10.20.20.0/24) com este Raspberry rodando `sim_raspberry.py`: descoberta,
    comando com ACK e heartbeat recebidos com sucesso.
- **Notebook provisionado:** apt atualizado, toolchain de build/áudio
  instalado (ffmpeg, portaudio19-dev, build-essential, etc.), venv do projeto
  criado e testado (Fase 1 completa rodando lá também), Ollama instalado com
  o modelo `llama3.2:3b` já baixado (2 GB, CPU-only — sem GPU dedicada).
  `jproma23` adicionado ao grupo `sudo` no notebook (não tinha nenhum
  privilégio antes). PlatformIO instalado neste Pi em venv própria
  (`~/.platformio-venv`) para compilar/gravar o firmware.
- **Próximo passo:** iniciar Fase 3 — Banco de dados e memória no Raspberry
  (Caps 15, 11): Database Manager (SQLite WAL no SSD), migrações
  versionadas, API de memória (remember/recall/update/forget/context/stats)
  exposta ao Notebook via comm.request, backup diário + réplica cruzada no
  Notebook.

- **Fase 3 concluída.** Implementado em `motion_core/memory/` (novo pacote
  Python — código do Raspberry, deploy separado do `src/orion/` do
  Notebook; `pyproject.toml` ganhou `"."` no `pythonpath` de teste para
  importar `motion_core.*`):
  - `schema.py` — todas as tabelas do Cap 15 s.3 e Cap 11 s.5 (pessoas,
    ambientes, objetos, conhecimento, conversas, configuracao_memoria,
    missoes, eventos, telemetria, logs, diagnosticos, configuracao,
    schema_version) + os índices mínimos do Cap 15 s.4, como migração
    versionada (`MIGRACOES`).
  - `database.py` — `DatabaseManager`: WAL, `PRAGMA foreign_keys`,
    `integrity_check`, recuperação em cascata (Cap 15 s.7: checkpoint do
    WAL → restaurar backup mais recente → recriar do zero em modo
    degradado), backup via API nativa do SQLite com rotação (7 diários + 4
    semanais), retenção (telemetria 30d, eventos 90d, logs 30d/erros 180d)
    com `VACUUM`. Deliberadamente síncrono (mais simples de testar) —
    quem chama em contexto assíncrono delega para `asyncio.to_thread`.
  - `manutencao.py` — ponte assíncrona com o Event Bus: `iniciar_banco()`
    publica `database.ready`/`database.rebuilt`; `TarefaManutencao` roda
    backup + retenção uma vez por dia no horário configurado
    (`database.backup_hour`), publicando `database.backup_completed` /
    `database.backup_failed`.
  - `api.py` — `MemoryAPI`: remember/recall/update/forget/context/stats
    (Cap 11 s.6), publicando `memory.updated`/`memory.recall_executed`.
    Toda exclusão vira log (Cap 11 s.8). `context()` monta pessoa +
    conversas recentes + conhecimento relevante (o resto do contexto de
    curto prazo — missão em execução, sensores — é RAM do Mission Core,
    não vem do banco).
  - `bridge.py` — `PonteMemoria`: liga comandos `memory.*` recebidos via
    `comm.mensagem.command` (Fase 2) à `MemoryAPI`, respondendo com
    RESPONSE — é assim que `memory.context()` chamado do Notebook chega
    até aqui.
  - `replica.py` — réplica cruzada do backup para o Notebook em blocos via
    `comm.send` (ACK + retransmissão, reusa o protocolo da Fase 2 em vez de
    um transporte de arquivos à parte); `ReceptorReplica` reconstrói por
    índice (não por ordem de chegada, já que retransmissão pode reordenar).
- **Bug de segurança encontrado e corrigido antes de ir para produção:**
  `MemoryAPI` interpolava nomes de coluna de `dados`/`filtro` direto na SQL
  - como esses dicts podem vir de uma requisição remota (`comm.request`),
  isso era brecha de SQL injection via nome de campo malicioso. Corrigido
  validando toda coluna contra `PRAGMA table_info` antes de montar a query
  (`ErroColunaInvalida`), com teste de regressão usando um nome de coluna
  contendo `; DROP TABLE ...`.
- **Bug real encontrado ao ligar `asyncio.to_thread` ao sqlite3:** conexões
  SQLite são presas à thread que as criou por padrão; como cada chamada
  assíncrona pode rodar em uma thread diferente do executor (mesma classe
  de problema já visto no pyserial da Fase 2), foi preciso abrir a conexão
  com `check_same_thread=False` — seguro aqui porque o acesso é sempre
  sequencial (nunca duas chamadas concorrentes na mesma conexão).
- **Testes:** 106 testes unitários + 5 de integração (`tests/integration/`).
  `conftest.py` (com `FakeTransporte`) subiu de `tests/unit/` para
  `tests/` para poder ser compartilhado com `tests/integration/`. Cenário
  ponta a ponta cobrindo backup → réplica → corrupção → recuperação, e
  benchmark confirmando `memory.context()` via `comm.request` abaixo de
  100 ms com massa de teste (500 conversas + 50 fatos).
- **Limitação conhecida, documentada no código:** a ponte de memória ainda
  não valida a origem da solicitação contra o Service Registry (Cap 14 s.9
  / Cap 11 s.8 exigem isso) — o Communication Core da Fase 2 ainda não
  implementa esse controle de acesso. Revisitar mais adiante.
- **Nota:** os testes usam `tmp_path` para o banco/backups, não os caminhos
  reais de `config/orion.yaml` (`/mnt/ssd/orion/...`) — isso será ligado de
  verdade quando o processo do Motion Core existir (Fase 7); por enquanto
  `motion_core/memory/` é uma biblioteca testável, ainda sem um daemon
  próprio rodando no boot.
- **Próximo passo:** iniciar Fase 4 — Hardware Core completo (Cap 10):
  firmware modular (Motor/Sensor/Radar/IMU/Encoder/Command Executor/
  Telemetry/Safety Manager), máquina de estados completa (BOOT→...→
  SAFE_MODE), todos os comandos de movimento com ACK e progresso, pacote
  periódico Radar Inteligente, segurança reativa (parada por distância
  mínima, inclinação, timeout) sem depender do Raspberry nem do Notebook.

## 2026-07-17 (continuação)

- **Fase 4 implementada (código completo), validação física ainda
  pendente.** Encontrado o guia de ligação elétrica do Sentinela X
  (`~/Downloads/Sentinela X — Guia de Ligação Elétrica.pdf`) com pinos
  reais para motores/ultrassom/IMU/DHT do mesmo Mega físico. Usado como
  base para `firmware/hardware_core/include/pins.h`.
- Implementado em `firmware/hardware_core/`:
  - `pins.h` — pinos confirmados (motores 2-6, HC-SR04 22/23, MPU6050 I2C
    20/21, DHT 24) e reservados (encoders 18/19, ultrassom traseiro 26/27,
    servo radar 9, pan/tilt 10/11, LED 25).
  - `estado.h` — máquina de estados completa do Cap 10 s.4 (BOOT→READY→
    IDLE→EXECUTING_MISSION→OBSTACLE_DETECTED→MISSION_PAUSED→ERROR→
    SAFE_MODE→SHUTDOWN), com notificação de transição ao Motion Core
    (EVENT `motion.status`).
  - `motor_manager.h` — 2 steppers via STEP/DIR/ENABLE, pulsos gerados por
    temporização com `micros()` (sem `delay()`), odometria aproximada por
    contagem de passos. `PASSOS_POR_METRO`/`PASSOS_POR_GRAU` são
    constantes de bancada (sem calibração real ainda — isso é a
    autocalibração da Fase 12).
  - `encoder_manager.cpp` — contagem de pulsos via interrupt (zero
    gracioso, sem hardware ligado ainda).
  - `sensor_ultrassonico.h` — driver HC-SR04 **não bloqueante** (maquina de
    estados; `pulseIn()` bloquearia até 1s por leitura, inaceitável no
    loop principal).
  - `radar_manager.h` — varredura do servo frontal (SCAN_FRONT) + leitura
    fixa usada pelo Safety Manager.
  - `imu_manager.h` / `dht_manager.h` — MPU6050 (Adafruit) e DHT11
    (Adafruit DHT); DHT só lê quando o robô está parado (a biblioteca
    bloqueia ~20-25ms por leitura).
  - `safety_manager.h` — parada reativa por obstáculo frontal, inclinação,
    impacto ou timeout de comando, **independente do Raspberry/Notebook**
    (Cap 6 regra 7).
  - `command_executor.h` — despacha MOVE_FORWARD/MOVE_DISTANCE/
    MOVE_CONTINUOUS/TURN_LEFT/TURN_RIGHT/STOP/DOCK/SCAN_FRONT/LIGHT_ON/OFF.
  - `telemetry_manager.h` — pacote Radar Inteligente (Cap 5 s.7) a cada
    500ms via TELEMETRY.
  - `main.cpp` reescrito integrando tudo sobre o protocolo/enquadramento
    da Fase 2. Compilado com PlatformIO: RAM 26,2% (2149/8192 bytes),
    Flash 15,0% (38160/253952 bytes) — bastante folga.
- **Descoberta importante (correção de contexto):** o guia de fiação
  descreve uma montagem **passada/planejada**, não o estado físico atual —
  o usuário confirmou "não temos hardware ainda". Só o Mega + cabo USB
  existem de verdade agora. Isso foi confirmado na prática: telemetria do
  Mega real mostra `imu_conectado=false`, `distancia_frontal_valida=false`,
  `dht_valido=false` de forma consistente (ausência real de hardware, não
  bug do firmware).
- **Validado no Mega real** (nível de protocolo/lógica, sem periféricos
  físicos): firmware gravado via PlatformIO; WHO_ARE_YOU, RETURN_STATUS e
  HEARTBEAT respondendo; MOVE_FORWARD/TURN_LEFT/STOP fazem a máquina de
  estados transicionar corretamente (IDLE↔EXECUTING_MISSION) com ACK e
  evento `motion.status`; LIGHT_ON/LIGHT_OFF ACKados; SCAN_FRONT completa
  e publica `motion.scan_complete` com as 7 leituras (todas inválidas,
  como esperado sem sensor real). Nenhum teste moveu nada fisicamente
  (nada de motor está conectado) - seguro de rodar sem risco.
- **Enviado para o usuário:** rascunho de e-mail (Gmail) com o guia de
  ligação elétrica completo, e o mesmo conteúdo salvo em
  `docs/hardware/wiring_arduino.md` (esse caminho era citado no PDF do
  Sentinela mas nunca tinha sido criado de fato).
- **Pendência real, não é código:** o critério de "pronto" da Fase 4
  ("bancada executa cada comando com rodas suspensas; obstáculo para o
  robô") exige motores/sensores fisicamente montados. Isso é responsabilidade
  do usuário montar o hardware (guia em mãos) - o firmware já está pronto
  para quando isso acontecer.
- **Próximo passo:** ou (a) aguardar a montagem física para validar o
  Cap 10 por completo, ou (b) adiantar a Fase 5 (Vision Core no Notebook,
  Cap 8) e Fase 6 (Voz + IA, Caps 9/7) em paralelo, já que essas rodam no
  Notebook e não dependem de hardware do Arduino estar montado.

## 2026-07-17 (Fase 5)

- **Fase 5 implementada e validada com webcam real.** Criado
  `src/orion/vision/`: `captura.py` (CapturaCamera assíncrona sobre
  cv2.VideoCapture, com descarte de frames de aquecimento — achado real: o
  primeiro frame lido logo após abrir a porta vem quase preto, a câmera
  ainda ajustando exposição/balanço de branco), `deteccao.py` (DetectorYolo,
  YOLOv8n), `reconhecimento_facial.py` (ReconhecedorFacial via
  `face_recognition`/dlib, embeddings comparados por distância euclidiana
  contra `pessoas.embedding_face`), `rastreamento.py` (Rastreador de um
  alvo único, com janela de tolerância antes de declarar "perdido"),
  `pan_tilt.py` (CalculadoraPanTilt: controle proporcional simples com
  limites de ângulo/velocidade, sem dependências pesadas), `vision_core.py`
  (orquestrador completo, publica todos os eventos do Cap 8 s.5,
  recuperação de câmera desconectada).
- **Extensão justificada no firmware:** adicionado o comando `SET_PAN_TILT`
  ao Mega (não estava na lista original do Cap 10 s.5, mas os servos
  pan/tilt já constavam no Cap 10 s.2 e o Cap 8 s.8 exige o comando) -
  testado no Mega real (ACK ok), servos ainda não montados fisicamente.
- **Dependências novas** (`pyproject.toml`, extra `vision`): opencv-python,
  ultralytics, face-recognition. Instalado no Notebook (dlib/face_recognition
  compilou por ~25min usando os 4 núcleos via LTO).
- **Bug de ambiente real encontrado e corrigido:** `face_recognition`
  chama `quit()` internamente (não levanta `ImportError`) quando falta
  `pkg_resources` - o `setuptools` mais recente (83.x) removeu esse módulo
  (deprecado, remoção prevista para 2025-11-30). Como `quit()` gera
  `SystemExit`, isso derrubava o processo `pytest` inteiro, não só pulava
  o teste. Corrigido fixando `setuptools<81` e instalando
  `face-recognition-models` direto do GitHub (não vem sozinho via pip) -
  ambos documentados em `pyproject.toml`.
- **Padrão de teste:** `pytest.importorskip` para cv2/ultralytics/
  face_recognition/numpy em todos os testes que precisam dessas libs -
  no Raspberry Pi (que nunca roda Vision Core) esses testes pulam
  graciosamente; no Notebook rodam de verdade. `VisionCore` ganhou
  parâmetros de injeção (`captura`, `detector`, `reconhecedor`,
  `timeout_alvo_perdido_s`) para testar a lógica de orquestração sem
  precisar de câmera/modelos reais.
- **Descoberta de hardware:** o Notebook tem DUAS câmeras físicas - a
  integrada ("PC Camera", índice 0/1) e, após o usuário conectar durante a
  sessão, a webcam USB externa dedicada à visão ("DV20 USB", índice 2/3).
  Adicionado `camera_indice_principal`/`camera_indice_luminosidade` em
  `config/orion.yaml` (Cap 17) para não fixar esses índices no código -
  podem mudar se os cabos forem replugados em outra ordem. Dois
  dispositivos de captura de áudio também presentes (mic da webcam +
  USB Audio Device) - relevante para a Fase 6.
- **Testado com a webcam real:** pipeline completo (captura → YOLO →
  reconhecimento facial → rastreamento → Event Bus) rodou sem erros contra
  `/dev/video2` de verdade; frame capturado e inspecionado (câmera
  apontada para o canto do teto, sem pessoa no quadro - por isso zero
  detecções, resultado correto para a cena, não um bug). Usuário optou por
  não repetir o teste com uma pessoa em frente à câmera por ora.
- **128 testes unitários passando no Notebook** (118 + 2 pulados no
  Raspberry, onde as libs de visão não são instaladas de propósito).
- **Pendência real, não é código:** assim como a Fase 4, falta a montagem
  física dos servos pan/tilt para fechar o critério de "pronto" por
  completo (SET_PAN_TILT já funciona no protocolo; falta o servo de
  verdade responder). Um teste com pessoa real em frente à câmera também
  fica para confirmar `vision.person_detected`/`person_recognized` de
  ponta a ponta.
- **Próximo passo:** Fase 6 — Voz e IA (Caps 9, 7): wake word "Fofão" +
  Whisper + Piper (offline), AI Manager via Ollama (já instalado com
  `llama3.2:3b` desde a Fase 2), Mission Planner. Dois microfones já
  disponíveis no Notebook para essa fase.

## 2026-07-17 (Fase 6)

- **Fase 6 implementada por completo (código); validação ao vivo bloqueada
  por hardware de áudio.** Criado `src/orion/voice/`: `captura_audio.py`
  (SeletorMicrofone - Cap 9 s.6, escolhe o melhor canal por RMS/estabilidade),
  `wake_word.py` (DetectorPalavraAtivacao - ver decisão abaixo),
  `transcricao.py` (Transcritor via faster-whisper), `sintese.py`
  (Sintetizador via Piper), `voice_core.py` (maquina de estados completa:
  IDLE→LISTENING→WAKE_DETECTED→TRANSCRIBING→THINKING→SPEAKING→ERROR,
  publicando todos os eventos do Cap 9 s.5), `audio_utils.py` (reamostragem
  compartilhada). Em `src/orion/mission/`: `ai_manager.py` (Ollama + prompt
  de sistema + contexto), `memory_client.py` (Notebook fala com a memória
  do Raspberry via comm.request, espelhando a MemoryAPI da Fase 3),
  `mission_planner.py` (fluxo de decisão do Cap 7 s.4: classifica
  comando/pergunta de hora/pergunta geral, consulta IA quando necessário,
  despacha comando ao hardware, registra a conversa na memória).
- **Decisão de design documentada no código:** não existe ainda modelo
  customizado openWakeWord treinado para "Fofão" (os modelos prontos da
  lib são em inglês; treinar um exige pipeline de dados sintéticos
  separado). Solução que funciona hoje, 100% offline: transcrever janelas
  curtas com faster-whisper e checar se "fofão" aparece no texto -
  `openwakeword` fica instalado como dependência para quando um modelo
  customizado existir.
- **Dependências novas** (`pyproject.toml`, extra `mission`): piper-tts,
  openwakeword. Voz baixada: `pt_BR-faber-medium` (63MB, via
  `python -m piper.download_voices`, salva em `data/piper_voices/` -
  gitignored, precisa rodar de novo em outra máquina).
- **2 bugs reais de taxa de amostragem encontrados e corrigidos:** tanto a
  gravação (`captura_audio.py`) quanto a reprodução (`sintese.py`) davam
  `PortAudioError: Invalid sample rate` - os dispositivos de áudio USB
  desta montagem só aceitam sua taxa nativa (44100Hz), não a que
  Whisper/Piper esperam (16kHz/22050Hz). Corrigido gravando/tocando na taxa
  nativa do dispositivo e reamostrando em software (interpolação linear,
  `audio_utils.reamostrar`, compartilhada entre os dois).
- **Notebook configurado para nunca dormir** (pedido do usuário): systemd
  sleep/suspend/hibernate/hybrid-sleep mascarados, logind ignorando
  tampa/idle/teclas de suspensão, Wi-Fi sem power-saving - ver
  `project_orion_os_wiring.md` na memória para os detalhes exatos.
- **164 testes passando no Notebook** (128 + 7 pulados no Raspberry, onde
  as libs de voz não são instaladas de propósito - Voice Core/Mission
  Core rodam só no Notebook).
- **Descoberta de hardware de áudio (bloqueia a validação ao vivo):**
  - Sem alto-falante físico disponível: o alto-falante interno e o P2
    (jack) do notebook não aparecem no sistema de áudio do Linux (falta
    driver/quirk do codec HDA Intel PCH - só HDMI aparece para esse chip).
    O "USB Audio Device" é só uma interface, sem caixinha conectada.
  - Microfones disponíveis (webcam integrada, USB Audio Device, webcam
    externa) captam sinal fraco demais (RMS ~0,008-0,01) para o Whisper
    transcrever de forma confiável, mesmo com o ganho do mixer ALSA já no
    máximo (95%) - testado repetidamente com o usuário falando perto e
    alto, sem sucesso na transcrição.
  - Usuário decidiu comprar um headset/caixinha USB com microfone
    embutido, resolvendo os dois problemas de uma vez. Planeja usar os
    mics atuais (integrado, webcams) como entradas secundárias para
    cancelamento de ruído no futuro (Cap 9 s.6 já prevê "fusão de áudio/
    beamforming quando o hardware permitir").
- **Pendência real, não é código:** o critério de "pronto" ("Fofão, que
  horas são?" com resposta falada; "Fofão, acenda a lanterna" aciona
  LIGHT_ON) exige áudio físico funcional dos dois lados (entrada e saída).
  Retestar assim que o headset USB chegar.
- **Próximo passo:** aguardar o headset USB para fechar a validação ao
  vivo da Fase 6, ou adiantar a Fase 8 (Avatar + interface web, Cap 13) -
  usuário já pediu um avatar de "cabeça cibernética" que acompanha
  pan/tilt na tela - ou revisitar as Fases 4/5 quando o hardware mecânico
  (motores, sensores, servos) for montado.

## 2026-07-17 (Fase 8, início — Avatar/Sentinelinha)

- **Sessão anterior caiu no meio da Fase 8** antes de logar aqui - esta
  entrada reconstrói o que ficou em disco (arquivos criados mas não
  commitados) e o que foi feito na retomada.
- **Primeira versão (perdida em decisão, mantida em código até o usuário
  mudar de ideia):** `avatar_server.py` (aiohttp) servindo uma "cabeça
  cibernética" 2D em canvas/CSS - visor tático/holográfico, sóbrio.
  Consumidor puro do Event Bus via SSE (`/eventos`), repassando
  `voice.status`, `motion.pan_tilt`, `motion.status`,
  `motion.obstacle_front`, `diagnostic.error`, `system.ready`.
- **Mudança de direção do usuário:** ele tinha, num rascunho de e-mail
  separado (assunto "Avatar robo"), um modelo próprio já pronto - a
  **Sentinelinha**, um mascote 3D (Three.js) fofo/flutuante (cabeça
  esférica, viseira, olhos com brilho, bochechas, antena pulsante,
  "orelhas" com anel ciano, corpo flutuante com anel propulsor),
  originalmente com rastreamento de rosto via webcam (diff de frames).
  Usuário decidiu: descartar a cabeça tática 2D e adotar a Sentinelinha
  como avatar oficial do Fofão, trocando o rastreamento por webcam pelos
  eventos reais do robô.
- **Bug real encontrado no modelo do usuário:** o HTML original carregava
  `three.min.js` via CDN (`cdnjs.cloudflare.com`) - quebra a regra
  "100% offline" do projeto (funciona só com internet). Corrigido: Three.js
  r128 baixado uma vez e vendorizado em
  `src/orion/display/static/vendor/three.min.js`, servido localmente pelo
  `AvatarServer` (que já serve todo `static/` como estático).
- **Reescrita de `static/index.html`, `avatar.css`, `avatar.js`:**
  geometria 3D da Sentinelinha mantida como o usuário desenhou; removida a
  parte de webcam/`getUserMedia`/detecção de movimento por diff de frame.
  No lugar:
  - `motion.pan_tilt` (evento real, publicado por `vision_core.py`) move a
    cabeça e os olhos, normalizado pelos limites reais dos servos
    (`config/orion.yaml`, seção `vision`: `pan_limits_degrees: [-80, 80]`,
    `tilt_limits_degrees: [-30, 45]`) - **não fixado no JS**: o avatar
    busca `/config` no servidor, que devolve esses limites lidos do YAML
    (rota nova em `avatar_server.py`), respeitando a regra arquitetural
    #6 (nenhum valor fixo fora de `config/orion.yaml`).
  - `voice.status` (estados reais de `voice_core.py`: IDLE, LISTENING,
    WAKE_DETECTED, TRANSCRIBING, THINKING, SPEAKING, ERROR) controla
    "energia"/animação e o texto do status; boca anima de verdade (abre e
    fecha) só durante SPEAKING.
  - `motion.obstacle_front` e `diagnostic.error` disparam um estado de
    alerta visual (olhos/antena ficam vermelhos) em vez do vermelho ser só
    decorativo.
- **`AvatarServer.__init__` ganhou o parâmetro `limites_pan_tilt`** (dict
  com os dois limites) e a rota `GET /config` que os expõe como JSON.
- **Teste novo:** `tests/unit/test_avatar_server.py`, com
  `pytest.importorskip("aiohttp")` (mesmo padrão de Vision/Voice - Display
  também é Notebook-only, EDR-0018). Cobre: `/` serve o HTML, `/config`
  devolve os limites certos, um evento publicado no bus chega de verdade
  no cliente SSE, e um tópico fora da lista repassada não gera assinatura
  no bus. **Não roda neste Raspberry** (sem `aiohttp` instalado de
  propósito aqui) - 128 testes passando + 8 pulados no Pi (contando os 4
  novos deste arquivo).
- **Novo `tools/preview_avatar.py`:** sobe o `AvatarServer` num Event Bus
  real e fica publicando uma sequência de eventos de mentirinha (pan/tilt
  varrendo em seno, estados de voz em ciclo IDLE→...→SPEAKING) - permite
  ver o avatar reagindo na tela sem precisar da Voice/Vision Core rodando
  de verdade. Mesmo padrão dos simuladores da Fase 2
  (`tools/sim_arduino.py`, `tools/sim_raspberry.py`).
- **Decisão consciente, não é pendência esquecida:** o avatar **não** foi
  ligado no `python -m orion` (`kernel/boot.py`/`__main__.py`) ainda.
  Motivo: `__main__.py` hoje faz o boot e encerra na hora (não existe
  ainda um loop principal de longa duração), e nem Vision nem Voice Core
  estão plugadas nesse boot apesar de já implementadas nas Fases 5/6 -
  ligar só o avatar sozinho nesse meio-termo ficaria pela metade. Revisitar
  quando o loop principal de execução for construído.
- **Pendência real:** tudo isso foi escrito e testado (onde dá) no
  Raspberry, mas Display é código do **Notebook** (Mission Core,
  EDR-0018) - nada disso rodou de fato num navegador ainda. Falta: no
  Notebook, `pip install -e ".[display]"` e `python tools/preview_avatar.py`,
  depois abrir `http://127.0.0.1:8090`.
- **Nada commitado ainda** - `avatar_server.py`, `static/`,
  `tools/preview_avatar.py` e `tests/unit/test_avatar_server.py` seguem
  como arquivos novos não versionados.
- **Próximo passo:** rodar `preview_avatar.py` no Notebook de verdade e
  validar visualmente; depois decidir com o usuário se a Sentinelinha
  também deve reagir a mais eventos (ex.: `motion.status` para
  IDLE/EXECUTING_MISSION), e revisitar a integração no boot quando o loop
  principal existir.

## 2026-07-17 (Fase 8, continuação — validado ao vivo no Notebook)

- **Acesso SSH do Raspberry para o Notebook estabelecido e confirmado**
  (`ssh jproma23@10.20.20.195`, sem senha - chave deste Pi já estava
  autorizada lá). Não existe git remote ligando os dois `~/orion-os`
  (cada máquina tem seu próprio histórico local) - sincronização de
  arquivos feita via `scp` direto. Detalhes salvos na memória do Claude
  (`reference_orion_os_notebook_ssh`) para não precisar redescobrir isso
  numa sessão futura.
- **2 bugs reais corrigidos em `tests/unit/test_avatar_server.py`** ao
  rodar de verdade no Notebook (aqui no Pi o teste só compila, não
  executa - falta `aiohttp` de propósito):
  1. Fixture assíncrona (`cliente`) precisava do decorator
     `@pytest_asyncio.fixture`, não `@pytest.fixture` puro - modo
     `asyncio_mode=strict` do projeto exige isso, senão dá
     `AssertionError` genérico no setup.
  2. O teste publicava um evento no bus sem o loop de despacho
     (`bus.iniciar()`) rodando - `publish()` só enfileira, quem entrega
     de fato ao assinante é a task de `iniciar()`. Sem ela, o teste só
     via o ping de keepalive 15s depois, nunca o evento. Corrigido
     criando a task no fixture e cancelando/aguardando no teardown.
- **170 testes passando no Notebook** (com `aiohttp` de verdade instalado
  - nenhum pulado lá, todas as libs de Vision/Voice/Display presentes).
- **Achado e encerrado um processo órfão de antes da sessão cair:**
  `/tmp/rodar_avatar.py` (script solto, fora do repositório) ainda estava
  rodando na porta 8090 com o avatar antigo (a cabeça tática 2D). Matado
  e substituído pelo `tools/preview_avatar.py` novo, versionado.
- **Validação visual real, pela primeira vez:** aberto Firefox em modo
  kiosk (`--kiosk`) na tela física do Notebook (`DISPLAY=:0`, sessão
  XFCE), apontado para `http://127.0.0.1:8090` - screenshots confirmam
  o Fofão renderizando de verdade: cabeça 3D girando com o pan/tilt
  simulado, antena pulsando, pill de status trocando de texto
  (ouvindo/entendendo/falando) em sincronia com o ciclo de estados do
  `preview_avatar.py`.
- **Renomeado de "Sentinelinha" para "Fofão"** (nome/título/fala de boas-
  vindas em `index.html` e `avatar.js`) - a pedido do usuário, para bater
  com a wake word já implementada na Fase 6 (Cap 9). Ressincronizado com
  o Notebook e Firefox reiniciado para pegar a mudança.
- **Efeito colateral resolvido, não era bug do avatar:** dois popups do
  painel do XFCE (`xfce4-panel`, tipo `_NET_WM_WINDOW_TYPE_POPUP_MENU`)
  ficaram travados abertos por cima da tela, sobrando de alguma interação
  anterior à sessão - descoberto via `xprop`/`xwininfo -tree` (não eram
  janelas do Firefox nem do avatar). Resolvido reiniciando o processo
  `xfce4-panel` no Notebook.
- **Estado atual:** avatar rodando ao vivo no Notebook, validado
  visualmente, mas ainda com dados simulados (`preview_avatar.py`) - não
  com Voice/Vision Core reais. Nada commitado ainda em nenhuma das duas
  máquinas.
- **Próximo passo:** decidir com o usuário se continua a Fase 8 (interface
  web do Raspberry - dashboard/conversa/mapa/diagnóstico, Cap 13 s.5-6 -
  e mapa polar do radar, nenhum dos dois começado ainda) ou se valida
  primeiro o avatar com a Voice Core real (Fase 6, pendente de headset
  USB) antes de seguir.

## 2026-07-17 (Fase 7, início — Motion Core / Navegação)

- **Decisão de sequenciamento com o usuário:** ao pedir pra continuar o
  projeto, verifiquei o `PLANO_IMPLEMENTACAO.md` e achei que a Fase 7
  (Motion Core/Navegação, Cap 12) está 0% feita, e que o painel MAPA da
  Fase 8 depende justamente dos dados dela (posição, radar). Perguntei ao
  usuário e ele confirmou: Fase 7 primeiro. Também confirmou que **não há
  motor físico montado ainda** - toda validação aqui é em nível de
  protocolo, igual às Fases 4/5.
- **Criado `motion_core/navigation/navigation_core.py`** (`NavigationCore`):
  máquina de estados dos 6 modos do Cap 12 s.3 (HOLD, MANUAL, GOTO,
  PATROL, FOLLOW, EXPLORE), publicando todos os eventos do Cap 12 s.11
  (`navigation.plan_created/segment_started/segment_completed/
  obstacle_avoided/target_lost/mode_changed/error`). Nunca fala com o
  Arduino direto - só via `ComunicacaoService` (`comm.send`/`request`),
  igual a regra arquitetural #1 exige.
  - **HOLD, MANUAL, GOTO, PATROL: implementação completa.** GOTO/PATROL
    fazem SCAN_FRONT antes de cada segmento (Cap 12 s.4) e abortam com
    `navigation.obstacle_avoided` se a leitura (varredura OU telemetria)
    indicar obstáculo abaixo de `motion.min_front_distance_cm`. PATROL
    tenta cada segmento até `navigation.obstacle_retry_max` vezes antes de
    desistir da rota.
  - **FOLLOW e EXPLORE: versão mínima deliberada**, documentada no
    docstring do módulo - FOLLOW só reage a `vision.person_detected` com
    uma correção proporcional simples e detecta perda por timeout (falta a
    rotação de busca do Cap 12 s.5 passo 5, que depende do Vision Core
    rodando de ponta a ponta); EXPLORE é só "escaneia e anda se livre", não
    um algoritmo de mapeamento de verdade (isso fica pro SLAM do Cap 12
    s.12, ORION OS 2.0+).
- **Entrada de missão via Event Bus:** `NavigationCore` assina
  `navigation.comando` (`{"acao": "GOTO", "graus":..., ...}`) - ainda não
  ligado ao recebimento real de COMMAND vindo do Notebook via TCP (isso é
  fiação da Fase 2/Communication Core, não desta fase); por ora a entrada é
  só esse tópico do Event Bus, testável e já correta arquiteturalmente.
- **19 testes novos** em `tests/unit/test_navigation_core.py`, usando
  `ComunicacaoService` real + `FakeTransporte` (mesmo padrão de
  `test_service.py`) com um auto-respondedor de ACK/scan em background -
  não um mock solto, testa o protocolo de verdade. 2 bugs reais de teste
  corrigidos no caminho (mesma classe de erro já visto antes: fixture
  assíncrona sem `@pytest_asyncio.fixture`, e checar eventos sem esperar
  `bus.aguardar_fila_vazia()` primeiro).
- **3 bugs reais encontrados testando contra o Mega físico** (script
  descartável, removido depois de validar) - nenhum aparece em teste
  unitário porque só se manifestam com timing real de hardware:
  1. `_executar_segmento` mandava `MOVE_DISTANCE` logo após o ACK do
     `SCAN_FRONT` - mas esse ACK só confirma que a varredura *começou*, não
     que terminou (leva ~2.1s: 7 ângulos × 300ms de assentamento do servo,
     `radar_manager.h`). **Corrigido:** `NavigationCore` agora espera o
     evento `motion.scan_complete` de verdade (com timeout de 3s e
     fallback pra lista vazia) antes de prosseguir - e além de corrigir o
     timing, isso também fecha um gap de spec real: antes a leitura do
     radar nunca era usada pra decidir nada, só o `SCAN_FRONT` era
     disparado e esquecido.
  2. Comandos de movimento (`TURN_*`, `MOVE_DISTANCE`) às vezes não eram
     ACKados quando enviados logo após o ACK de um comando de movimento
     anterior - o ACK confirma só que o comando foi *recebido*, o firmware
     ACKa na hora e executa de forma assíncrona (`Estado::EXECUTING_MISSION`
     → `Estado::IDLE`). **Corrigido:** `NavigationCore` agora assina
     `motion.status` e espera `Estado::IDLE` antes de encadear o próximo
     comando de movimento (`_aguardar_ocioso`, timeout de 10s).
  3. **Pendência real, não resolvida:** mesmo com os dois fixes acima,
     ainda observei (não sempre, parece intermitente) falha de ACK logo
     após um `SCAN_FRONT` completar - às vezes no comando seguinte
     (`MOVE_DISTANCE`), às vezes no próprio `SCAN_FRONT` de um segmento
     posterior. Adicionei uma folga fixa de 0.3s após `motion.scan_complete`
     (`PAUSA_APOS_SCAN_S`) como mitigação pragmática, mas isso **não
     eliminou o problema por completo** - parece uma flakiness real de
     baixo nível (buffer serial/CH340, ou o firmware sob timing de carga),
     não mais um bug determinístico de ordenação que dá pra resolver só no
     lado Python. Fica como item aberto: investigar o lado firmware
     (`main.cpp`/`radar_manager.h`) numa sessão dedicada, ou considerar
     aumentar `max_retries`/`ack_timeout_ms` especificamente pro link
     Raspberry↔Arduino.
- **185 testes passando no Notebook** (170 + 15 novos), **143 passando + 8
  pulados no Raspberry** (os pulados continuam sendo só os de
  Vision/Voice, de propósito).
- **Não commitado ainda.**
- **Próximo passo:** decidir entre (a) investigar a flakiness do item 3
  acima com mais tempo/instrumentação no firmware, (b) seguir pra Fase 8
  de verdade (interface web do Raspberry) usando o que já existe de Fase 7,
  ou (c) fundir sensores pra `motion.position` (Cap 12 s.8), que nenhuma
  das duas fases anteriores cobre ainda.

## 2026-07-17 (aparte — Pi Connect caindo, não é do ORION OS)

- Usuário reportou queda constante do compartilhamento de tela via
  Raspberry Pi Connect (ele está 100% remoto, sem acesso físico de
  fallback). Diagnosticado: não tem relação com o código deste projeto -
  é o `wayvnc` (servidor VNC local usado pelo Pi Connect) perdendo a saída
  HDMI-A-1 (a TV usada como monitor) repetidamente e crashando -
  `rpi-connect-wayvnc.service` já tinha reiniciado 127+ vezes no dia
  (`journalctl --user`, mensagens "Selected output HDMI-A-1 went away" /
  "No fallback outputs left. Exiting").
- Correção padrão (`hdmi_force_hotplug=1` em `/boot/firmware/config.txt`)
  exige reboot do Pi - **não aplicada**: o usuário está sem acesso físico
  de fallback, então um reboot arriscado (rede não voltar, sessão gráfica
  não subir) o deixaria sem jeito nenhum de recuperar o Pi. Detalhes e essa
  restrição crítica salvos na memória do Claude
  (`project_pi_hdmi_wayvnc_crashloop`) para não arriscar de novo sem
  reconfirmar com o usuário no momento.
- Parou de cair sozinho (TV provavelmente estabilizou o sinal) - usuário
  optou por seguir sem aplicar o fix por ora.

## 2026-07-17 (fechando o item 3 da Fase 7 - flakiness do ACK)

- Retomada a investigação do item 3 pendente (falha intermitente de ACK
  logo após SCAN_FRONT). Revisado `main.cpp`/`radar_manager.h`/
  `sensor_ultrassonico.h` do firmware por completo: nenhum bloqueio
  (`delay()`/`pulseIn()`) encontrado - `loop()` drena todo o Serial
  disponível antes de qualquer outra coisa, sensor ultrassônico é uma
  máquina de estados não-bloqueante de verdade. Nada óbvio ali.
- Usuário reiniciou o Arduino fisicamente (confirmado seguro - não afeta
  Raspberry nem acesso remoto, são componentes independentes).
- Rodado um diagnóstico com timeout generoso (5s) medindo o tempo real de
  cada ACK em 10 ciclos SCAN_FRONT+MOVE_DISTANCE: **0 falhas em 10**,
  ACKs consistentes em 100-210ms (bem abaixo de qualquer timeout usado),
  **0 quadros inválidos** no decodificador. Também conferido
  `vcgencmd get_throttled` = `0x0` - descarta queda de energia/throttling
  do Pi como causa.
- **Conclusão:** não é mais tratável como bug determinístico de ordenação
  (os 2 fixes já aplicados nesta fase - esperar `motion.scan_complete` e
  `Estado::IDLE` antes de encadear comando - continuam corretos e válidos
  por si só, independente disso). O que sobra parece ser um glitch raro e
  não-reproduzível sob demanda (talvez ruído elétrico pontual no adaptador
  CH340, ou um hiccup ocasional do driver USB do kernel) - não uma falha
  sistemática. Na configuração de produção (`config/orion.yaml`:
  `max_retries: 3`, `ack_timeout_ms: 500`), o mecanismo de retransmissão
  que o Communication Core já tem (Fase 2) deve absorver esse tipo de
  blip raro sem intervenção adicional - um ACK que demora 100-210ms tem
  folga enorme dentro de 500ms, e uma retransmissão isolada resolve o
  resto. **Item encerrado** - não fica mais como pendência aberta, mas
  sem "causa raiz" 100% identificada (aceito como characteristic
  esperado de um link serial USB real, coberto pela retransmissão
  existente).

## 2026-07-17 (Cap 12 s.8 - Fusão de Sensores / motion.position)

- Implementado `motion_core/navigation/fusao_sensores.py` (classe
  `FusaoSensores`), retomando a opção (c) deixada em aberto na entrada
  anterior desta mesma data. Módulo separado do `NavigationCore` (regra
  #9 do ARQUITETURA.txt - uma responsabilidade cada), mas seguindo o mesmo
  padrão de construtor/assinatura de eventos: recebe `event_bus` e a
  fatia `config.secao("motion")`, assina `comm.mensagem.telemetry`.
- **O que faz:**
  1. A cada telemetria com `passos_esquerda`/`passos_direita`, calcula o
     delta desde a leitura anterior e atualiza pose (x, y, orientação)
     por **odometria diferencial clássica** (fórmula padrão:
     `v = (v_dir+v_esq)/2`, `ω = (v_dir-v_esq)/wheel_base`), com
     integração por ponto médio (usa a orientação na metade do
     movimento para projetar x/y, erro menor que Euler simples).
     Publica `motion.position` com `x_m`, `y_m`, `orientacao_graus`,
     `velocidade_m_s`. Usa `motion.steps_per_meter` e
     `motion.odometry_correction_factor` já existentes (fator ajustado
     pela autocalibração do Cap 12 s.9, ainda não implementada).
  2. A cada telemetria com `imu_conectado=true`, verifica
     `inclinacao_graus` contra `motion.tilt_limit_degrees` e
     `impacto_detectado`. Em perigo, publica **`safety.safe_mode_entered`**
     (`Prioridade.CRITICA`) - usei o evento já definido no Cap 18 s.9
     para isso (mais específico que `diagnostic.error`, que é genérico
     de falha de módulo). Volta a normalizar publica
     `safety.safe_mode_exited`. Edge-triggered (só publica na mudança de
     estado) para não spammar a cada 500ms enquanto a condição persiste.
- **Config novo:** adicionado `motion.wheel_base_m: 0.30` em
  `config/orion.yaml` - distância entre as rodas, necessária pra fórmula
  de rotação diferencial e que não existia na configuração. Marcado
  explicitamente como **PLACEHOLDER** no comentário do YAML: nenhuma
  roda/encoder físico está montado ainda, então é só um valor plausível
  para um chassi pequeno - medir e ajustar quando o chassi for montado
  de verdade (regra #6 do ARQUITETURA.txt: nada de valor físico fixo no
  código - pelo menos agora está no YAML, não hardcoded).
- **Decisão de escopo (documentada no docstring do módulo):** o Cap 12
  s.8 fala em combinar "orientação... da MPU6050" na fusão, mas a
  telemetria de hoje (`telemetry_manager.h`) só expõe
  `inclinacao_graus` (ângulo de inclinação do chassi) e
  `impacto_detectado` - **não há yaw/heading nem giroscópio bruto**
  disponível no pacote. Não dá pra fazer fusão de rumo com a IMU de
  verdade com o dado que existe hoje. Por isso: odometria por encoder
  pura para (x, y, orientação), IMU usada só para a detecção de
  segurança, nunca para corrigir o rumo calculado. Mesmo padrão de
  "mínimo viável + gap documentado" já usado em FOLLOW/EXPLORE do
  `navigation_core.py`. Também não há um sinal de "tombamento"
  separado do firmware - inclinação acima do limite cobre os dois
  casos (perigosa e tombamento extremo) por ora.
- **Testes:** `tests/unit/test_fusao_sensores.py`, 12 casos novos, com
  telemetria sintética (sem motores/encoders físicos montados - mesma
  situação de sempre nesta fase): reta sem giro, giro proporcional à
  diferença de passos (fórmula conferida com `math.degrees` no próprio
  teste), fator de correção de calibração aplicado, telemetria sem
  campos de encoder ignorada, contador de passos regredindo (ex.: Mega
  reiniciou) resincroniza sem publicar posição fantasma, inclinação/
  impacto disparando e normalizando `safe_mode_entered`/`exited`, e não
  republicar a cada telemetria enquanto o perigo persiste.
- **143 + 12 = 155 testes passando + 8 pulados no Raspberry** (Vision/
  Voice, de propósito). `ruff check` limpo nos arquivos tocados.
- **Não testado com deslocamento real** (sem motores/encoders montados
  - só a parte de segurança da IMU pode ser validada com o Mega físico
  de verdade, já que a MPU6050 está conectada). Cap 12 s.8 fica com a
  fusão implementada e testada em nível de protocolo, mas **sem
  validação com movimento físico real** - normal nesta fase, mesmo
  padrão dos demais itens de Fase 7.
- **`PLANO_IMPLEMENTACAO.md` não alterado**: o "pronto quando" da Fase 7
  inteira ainda depende de patrulha real com desvio, FOLLOW mantendo
  distância e autocalibração rodando de verdade - nenhum desses itens
  está pronto (sem motores montados). Fusão de sensores é só uma peça
  do que falta.
- **Próximo passo:** autocalibração (Cap 12 s.9) quando houver motores
  montados, ou avançar pra Fase 8 (interface web) usando o que já
  existe. `motion.position` e os eventos `safety.safe_mode_*` já estão
  publicados no Event Bus para quando a interface (Cap 13) ou o Mission
  Core quiserem consumi-los.

## 2026-07-17 (Fase 8, continuação — Dashboard web do Raspberry)

- **Criado `motion_core/webui/`** (`WebUIServer`, Cap 13 s.4-5): servidor
  aiohttp rodando **no Raspberry** (diferente do avatar, que é
  Notebook-only) - primeira parte da interface web, o painel DASHBOARD.
  Diferente do avatar (repassador puro sem estado), este módulo mantém um
  pequeno cache em memória do último valor de cada coisa relevante
  (`_estado`), porque quem abre a página precisa ver o estado ATUAL na
  hora, não só esperar o próximo evento - ainda assim nenhuma lógica de
  DECISÃO mora aqui (Cap 13 s.2), só agregação pra exibição.
- **Rotas:** `GET /` (HTML), `GET /estado` (snapshot JSON agregado + últimos
  30 eventos, pra quem acabou de conectar), `GET /eventos` (SSE, mesmo
  padrão do avatar). Consome `system.*`, `motion.*`, `navigation.*`,
  `vision.*`, `voice.*`, `diagnostic.*` (Cap 13 s.7) **e `safety.*`**
  (evento novo que a Fusão de Sensores passou a publicar hoje mesmo) e
  `comm.mensagem.telemetry`.
- **Painéis implementados (Cap 13 s.5):** Sistema (modo/estado), Segurança
  (SAFE_MODE ativo/motivo), Telemetria (distância/temperatura/umidade/
  inclinação - bateria/energia mostrada como "não disponível nesta
  versão", honesto em vez de inventar), Posição estimada (x/y/orientação/
  velocidade, direto do `motion.position` da Fusão de Sensores),
  Missão/Voz/Visão, e um log dos últimos eventos.
- **`aiohttp` instalado de verdade neste Raspberry** (extra `display` do
  `pyproject.toml`) - diferente do avatar, que roda só no Notebook, o
  dashboard roda aqui, então os testes rodam de verdade aqui também (não
  pulam mais).
- **9 testes novos** em `tests/unit/test_webui_server.py`, mesmo padrão do
  `test_avatar_server.py` (`TestClient`/`TestServer`, sem mock solto).
  Acertei o fixture assíncrono (`@pytest_asyncio.fixture`) de primeira
  desta vez - já tinha caído nesse erro duas vezes antes nesta sessão.
- **168 testes passando + 7 pulados no Raspberry** (os 4 do avatar saíram
  da lista de pulados agora que `aiohttp` está instalado aqui; sobrou só
  Vision/Voice/IA, que continuam Notebook-only de propósito) - **206
  passando no Notebook** (0 pulados lá).
- **Limpeza:** removido `motion_core/src/` (scaffold morto do zip
  original - diretórios vazios `bridge/memory/navigation/webui` sem
  nenhum arquivo, nunca referenciados em lugar nenhum) - o código de
  verdade sempre esteve em `motion_core/memory/`, `motion_core/navigation/`
  e agora `motion_core/webui/`, direto, sem esse `src/` a mais.
- **Novo `tools/preview_webui.py`** (mesmo espírito do
  `preview_avatar.py`): sobe o servidor com telemetria/posição/missão/
  SAFE_MODE de mentirinha em ciclo, pra ver o dashboard funcionando sem
  precisar do robô inteiro.
- **Validado ao vivo de verdade:** aberto num Firefox separado no
  Notebook (mesma rede, `http://10.20.20.185:8080` - o Raspberry
  respondendo por IP, não só localhost, confirmando "acessível de
  qualquer dispositivo da rede local" do Cap 13 s.4) e depois colocado
  em modo kiosk **na tela principal do Notebook, no lugar do Fofão**
  (a pedido do usuário, temporariamente) - screenshots confirmam os
  painéis atualizando ao vivo via SSE.
- **Ajuste de CSS no caminho:** o layout inicial de rótulo/valor
  (`grid-template-columns: auto 1fr`) quebrava palavras longas de forma
  feia (`overflow-wrap: anywhere` cortando no meio da palavra); trocado
  por uma coluna de rótulo mais larga (`minmax(auto, 42%)`) e
  `word-break: break-word`, que quebra em pontos melhores.
- **Não implementado ainda (Cap 13 s.4):** as páginas CONVERSA, MAPA
  (radar polar), DIAGNÓSTICO e CONFIGURAÇÃO - só o DASHBOARD existe por
  ora. `PLANO_IMPLEMENTACAO.md` não alterado (o item da Fase 8 cobre as
  5 páginas + mapa do radar, nenhum dos dois checkboxes está totalmente
  atendido ainda).
- **Não commitado ainda.**
- **Próximo passo:** CONVERSA (histórico de conversa via `memory_client`/
  Fase 3), MAPA (radar polar - já dá pra usar `motion.scan_complete`),
  DIAGNÓSTICO (heartbeats/últimos erros/log, Cap 16), CONFIGURAÇÃO
  (acesso restrito) - ou trocar o Notebook de volta pro avatar quando o
  usuário quiser.
- **Simplificado a pedido do usuário logo em seguida:** removido o painel
  "Missão/Voz/Visão" (Voz e Visão sempre vazios nesta fase - Voice/Vision
  Core não rodam no Raspberry) e a linha "Bateria/energia" (sempre "não
  disponível") - "último passo da missão" foi pro painel Sistema. Ficou
  em 4 painéis (Sistema, Segurança, Telemetria, Posição) + o log de
  eventos, em vez de 5 + log. O servidor (`server.py`, `/estado`) continua
  expondo `voz`/`visao` no JSON - só a página não mostra mais, caso uma
  futura página (CONVERSA) queira usar. Testes do servidor inalterados (9
  passando) - só HTML/JS mudaram. Recarregado no kiosk do Notebook,
  confirmado por screenshot.

## 2026-07-17 (Fase 8, continuação — Mapa polar do radar)

- **Nova página `motion_core/webui/static/mapa.html`/`mapa.js`/`mapa.css`**
  (Cap 13 s.4: "MAPA — radar polar (0°–180°), posição e orientação
  estimadas") - consumidor puro via SSE, igual as outras páginas.
  Desenha em `<canvas>`: anéis de alcance a cada 50cm (até 200cm, escala
  fixa por ora), raios nos 7 ângulos de leitura, o robô como triângulo no
  centro apontando "pra frente", e o polígono conectando as leituras
  válidas da última `motion.scan_complete` (leituras inválidas viram um
  ponto apagado na borda em vez de sumir, pra deixar claro que aquele
  ângulo não tem dado, não que está livre).
- **`WebUIServer`:** adicionada rota `GET /mapa` e `motion.scan_complete`
  passou a atualizar `_estado["mapa"]["leituras"]` (já estava na lista de
  tópicos consumidos, só não gravava em lugar nenhum ainda).
- **Navegação entre páginas:** cabeçalho comum (Dashboard/Mapa) adicionado
  em `index.html` e `mapa.html`, com a página atual destacada.
- **2 testes novos** (`GET /mapa` serve HTML; `motion.scan_complete`
  atualiza o estado) - 11 no arquivo do servidor web, 170 passando + 7
  pulados no total do Raspberry.
- **`tools/preview_webui.py`** ganhou uma varredura simulada (7 ângulos,
  distância variando em seno, leitura central "some" de vez em quando pra
  testar o caso de leitura inválida) - sem isso o mapa não tinha o que
  desenhar.
- **Validado ao vivo:** kiosk do Notebook trocado pra `/mapa` a pedido do
  usuário ("cadê o mapa") - screenshot confirma o polígono desenhando
  certo, os rótulos dos anéis, e "7/7 leituras válidas" no rodapé.
- **Não commitado ainda.**
- **Próximo passo:** CONVERSA (Fase 3/`memory_client`) e DIAGNÓSTICO (Cap
  16 - heartbeats, últimos erros, log) são as duas páginas que faltam;
  CONFIGURAÇÃO (acesso restrito, Cap 13 s.4) é a última. `mapa.js` tem uma
  limitação conhecida a documentar quando tiver dado real: a escala fixa
  de 200cm é um chute razoável pra ambiente interno, não vem do
  `config/orion.yaml` ainda - revisitar se precisar de alcance maior.

## 2026-07-17 (Fase 8, continuação — Diagnóstico e Conversa)

- **Página DIAGNÓSTICO** (Cap 13 s.4-5): `GET /diagnostico` +
  `GET /log`. Passou a consumir `diagnostic.error`, `comm.link_degraded`
  (últimos erros), `comm.module_lost`/`comm.module_recovered` (saúde dos
  módulos - "ok"/"perdido" por nome, com timestamp). `GET /log` expõe as
  últimas N linhas (padrão 200, máx. 2000) de `data/logs/orion.log`
  **somente leitura** (Cap 13 s.4) - se o arquivo não existir ainda,
  devolve aviso em vez de erro.
- **Página CONVERSA** (Cap 13 s.4: "transcrição da interação por voz"):
  `GET /conversa` + `GET /api/conversas`. Diferente das outras páginas,
  não guarda o histórico em memória - consulta a **Fase 3 (Memory Core)
  direto** via `MemoryAPI.recall("conversas", ...)`, porque como o
  servidor roda no próprio Raspberry, dá pra ler o banco local sem
  precisar de rede (Cap 13 s.4: "consultas de histórico... locais ao
  SSD"). `WebUIServer` ganhou um parâmetro opcional `memory_api` -
  quando `None` (banco não montado, como agora nesta máquina de dev, sem
  SSD em `/mnt/ssd/orion/`), a página mostra aviso em vez de quebrar.
  Novos balões de conversa chegam via `memory.updated` (evento que
  `MemoryAPI.remember()` já publicava desde a Fase 3) - o evento só avisa
  "recarregue", o dado de verdade sempre vem da API, nunca duplicado.
- **9 testes novos** (diagnóstico: erro registrado, saúde de módulo
  atualizada, `/log` bem formado; conversa: HTML serve, aviso sem
  `memory_api`, histórico real com um `DatabaseManager`/`MemoryAPI` de
  teste em `tmp_path` - mesmo padrão do `test_memory_database.py`).
  **177 passando + 7 pulados no Raspberry, 215 passando no Notebook.**
  `ruff` limpo.
- **Todas as 4 páginas** (Dashboard/Mapa/Diagnóstico/Conversa) com
  navegação cruzada no cabeçalho agora.
- **Não commitado ainda.**
- **Falta só CONFIGURAÇÃO** (Cap 13 s.4: "acesso restrito") pra fechar a
  Fase 8 por completo - decisão de design pendente (que tipo de
  restrição de acesso, já que o projeto não tem autenticação em lugar
  nenhum ainda). Usuário pediu pra deixar a revisão visual de tudo
  (colocar cada página na tela) pra depois de terminar de construir.

## 2026-07-17 (Fase 8, continuação — Configuração: as 5 páginas completas)

- **Página CONFIGURAÇÃO** (Cap 13 s.4: "parâmetros do sistema, acesso
  restrito"): `GET /configuracao` + `GET /api/configuracao`. Perguntei ao
  usuário como deveria funcionar o "acesso restrito" (projeto não tem
  autenticação em lugar nenhum) - escolheu **restringir por IP: só
  responde a pedidos vindos do próprio Raspberry** (`127.0.0.1`/`::1`),
  não do resto da rede local (diferente das outras 4 páginas). Fora do
  Raspberry, devolve HTTP 403. Somente leitura por ora - editar
  configuração ao vivo (com validação/reinício) fica pra uma iteração
  futura, mesmo espírito de escopo mínimo já usado em FOLLOW/EXPLORE e no
  log do DIAGNÓSTICO.
- **`WebUIServer` ganhou parâmetro opcional `config: ConfigurationManager
  | None`** - quando presente, `/api/configuracao` devolve
  `config.bruto()` (todo o `config/orion.yaml` parseado); quando `None`,
  mostra aviso em vez de quebrar (mesmo padrão de `memory_api`).
- **7 testes novos**: checagem de acesso local isolada (aceita
  `127.0.0.1`/`::1`, rejeita IP de rede), acesso via `TestClient` (que
  conecta por loopback de verdade, prova o caminho "permitido" ponta a
  ponta), API sem/com `ConfigurationManager` real carregado do
  `config/orion.yaml` de verdade. **182 passando + 7 pulados no
  Raspberry, 220 passando no Notebook.** `ruff` limpo.
- **As 5 páginas do Cap 13 s.4 existem agora**: DASHBOARD, MAPA,
  DIAGNÓSTICO, CONVERSA, CONFIGURAÇÃO, com navegação cruzada entre todas.
- **`PLANO_IMPLEMENTACAO.md` não alterado** - o "pronto quando" da Fase 8
  ("interface reflete eventos em < 500ms acessada do celular na rede
  local") foi demonstrado com SSE (latência bem abaixo de 500ms) e acesso
  de outro dispositivo na rede (o Notebook, via IP), mas **não com um
  celular de verdade** ainda - e o item "Acesso remoto via Raspberry Pi
  Connect documentado" nem foi começado. Nenhum checkbox da Fase 8
  marcado ainda, por essas duas razões.
- **Não commitado ainda.**
- **Próximo passo:** o usuário pediu pra fazer uma revisão visual de
  tudo (colocar cada página na tela) antes de continuar - isso vem antes
  de qualquer trabalho novo.

## 2026-07-17 (marco — sistema inteiro rodando de ponta a ponta pela primeira vez)

- **Usuário autorizou trabalho autônomo** ("continue sozinho, sem
  perguntar") e retomou a decisão adiada mais cedo hoje: WiFi como link
  principal Notebook↔Raspberry, com o Motion Core rodando de verdade.
  Guardado em memória (`feedback_orion_os_dont_ask_proceed`): não usar
  mais `AskUserQuestion` neste projeto para decisões de implementação,
  só para bloqueios reais (acesso físico, credenciais, ações destrutivas
  irreversíveis como reiniciar o Raspberry).
- **Criado `motion_core/__main__.py`** - processo principal do Raspberry
  (equivalente ao `python -m orion` do Notebook), rodando com
  `python -m motion_core`: sobe o servidor TCP (Cap 14 s.2), a ponte
  serial com o Arduino (com fallback tolerante se a porta não abrir ou o
  Arduino não responder WHO_ARE_YOU), `NavigationCore` + `FusaoSensores`
  (Fase 7), o banco de dados se o SSD estiver montado, e o `WebUIServer`
  (Fase 8) - tudo num único Event Bus. Roda até Ctrl+C/SIGTERM, com
  desligamento limpo de tudo.
- **`config/orion.yaml`:** `communication.raspberry.host` trocado de
  `192.168.50.2` (sub-rede Ethernet de produção, EDR-0018, ainda não
  existe fisicamente) para `10.20.20.185` (IP real do Raspberry na rede
  WiFi atual) - comentário no YAML deixa claro que isso é o link de
  desenvolvimento, não uma mudança de arquitetura.
- **`src/orion/kernel/boot.py`:** a etapa "Detecção do Raspberry Pi"
  (Fase 2), que desde a Fase 1 só logava "não implementado", agora tenta
  de verdade conectar via TCP + `WHO_ARE_YOU` (`_conectar_raspberry`),
  com timeout de 3s e tolerância a ausência (Cap 6 s.8) - Raspberry
  desligado ou fora da rede não trava nem aborta o boot do Notebook.
  `SistemaOrion` ganhou os campos `comm` e `raspberry_conectado`.
- **VALIDADO DE VERDADE, PELA PRIMEIRA VEZ NO PROJETO:** `python -m
  motion_core` rodando aqui no Raspberry (com o Arduino real conectado
  via WHO_ARE_YOU) + `python -m orion` rodando no Notebook real, ao
  mesmo tempo, por WiFi de verdade - log do Notebook:
  `Descoberta OK: destino=motion_core nome=motion_core versao_modulo=0.1.0`
  /  `Raspberry (Motion Core) conectado: motion_core v0.1.0`. As três
  pontas do Fofão (Notebook, Raspberry, Arduino) nunca tinham
  conversado ao mesmo tempo antes de hoje.
- **Achado real, não corrigido ainda:** como `python -m orion` hoje ainda
  é um boot único que conecta e encerra na hora (não é um processo de
  longa duração - decisão registrada anteriormente no jornal), o
  `MonitorHeartbeat` do Raspberry continua tentando mandar heartbeat pro
  Notebook depois que ele já desconectou, gerando avisos repetidos
  (`Falha ao enviar heartbeat` / `socket.send() raised exception`) em vez
  de detectar o link morto e desistir. Não bloqueia nada hoje (o processo
  encerra limpo do mesmo jeito quando eu mando SIGTERM), mas é uma
  robustez real faltando em `MonitorHeartbeat` (Fase 2,
  `src/orion/communication/heartbeat.py`) - ele só detecta perda pela
  ausência de heartbeats *recebidos*, não por falha ao *enviar*. Fica
  como pendência aberta.
- **5 testes novos** de integração (`tests/integration/test_motion_core_main.py`,
  marcador `sim`): conectar no Arduino com sucesso (pty simulando o
  firmware, mesmo padrão da Fase 2), tolerar porta inexistente, tolerar
  porta existente mas sem resposta WHO_ARE_YOU, abrir memória com/sem SSD
  disponível. **192 passando + 7 pulados no Raspberry, 230 passando no
  Notebook.** `ruff` limpo.
- **Não commitado ainda.**
- **Próximo passo:** corrigir o `MonitorHeartbeat` pra detectar falha de
  envio como perda de link (achado acima); ou fazer `python -m orion`
  virar um processo de longa duração de verdade, pra a conexão com o
  Raspberry durar mais que alguns segundos; ou seguir para a Fase 9
  (Diagnóstico e Segurança, Caps 16/18).

## 2026-07-17 (correção do achado do MonitorHeartbeat + bug de import circular)

- **Corrigido o achado de robustez do `MonitorHeartbeat`** registrado na
  entrada anterior: `enviar_heartbeat()` falhando (link fechado do outro
  lado) agora também conta como perda - unificado com a detecção por
  ausência de recebimento na mesma função `_marcar_perdido` (idempotente
  via `_perdidos_atualmente`, publica `comm.module_lost` só uma vez).
  Antes, um peer desconectado gerava aviso de log pra sempre e nunca
  disparava `comm.module_lost`.
- **Teste de regressão novo:** `test_falha_ao_enviar_heartbeat_tambem_gera_comm_module_lost`
  (`tests/unit/test_heartbeat.py`) - monitora um peer sem nunca registrar
  o link (`enviar_heartbeat` sempre falha com "sem rota"), confirma que
  `comm.module_lost` é publicado uma única vez, não repetido a cada
  tentativa.
- **Bug real encontrado e corrigido no caminho:** ao adicionar os imports
  de `orion.communication.*` no topo de `src/orion/kernel/boot.py`
  (entrada anterior), isso quebrou a importação do pacote inteiro
  `orion.kernel` com um ciclo de import - `orion/kernel/__init__.py`
  importa `boot.py` de cara, `boot.py` importava `orion.communication.*`,
  que importa `orion.kernel.event_bus`, fechando o ciclo. Corrigido
  movendo esses imports pra dentro das funções que os usam (import local,
  com um comentário explicando o motivo) e usando
  `if TYPE_CHECKING: from orion.communication.service import
  ComunicacaoService` só para as anotações de tipo continuarem
  funcionando sem re-introduzir o ciclo em tempo de execução.
- **193 testes passando + 7 pulados no Raspberry, 231 passando no
  Notebook.** `ruff` limpo. Item do jornal anterior fechado - não é mais
  pendência aberta.
- **Não commitado ainda.**

## 2026-07-18 (primeiro teste do pan/tilt no hardware físico + bug real no firmware + investigação de brownout)

- **Renomeação:** todo o texto do repositório que dizia "ORION X" (nome
  provisório do robô usado nos e-mails/specs até ontem) foi trocado para
  **Fofão** - `README.md`, `ARQUITETURA.txt`, `docs/ses/`, `docs/edr/`,
  `config/orion.yaml` (`robot_name`), testes e `docs/hardware/`. Conferido
  antes que `robot_name` só é usado como texto de exibição (logs, web UI),
  sem validação de valor fixo - troca segura. 183 testes passando + 7
  pulados, `ruff` limpo depois da troca.
- **Hardware físico do pan/tilt chegou hoje** (usuário tinha avisado
  ontem que chegaria "amanhã") - primeira vez testando os dois servos
  reais, ainda sem os motores de passo (esses ainda não foram montados).
- **Bug real encontrado e corrigido no firmware:** `CommandExecutor` é um
  objeto global (`comandos`, em `main.cpp`) e fazia `pinMode()` +
  `Servo::attach()` + `Servo::write()` direto no construtor. Construtores
  de objetos globais em C++/Arduino rodam **antes** de `main()` chamar
  `init()` (que configura os timers usados pelo PWM) - então o `init()`
  pisava a configuração do timer que o `Servo::attach()` tinha acabado de
  fazer, e o servo nunca respondia fisicamente, mesmo com o firmware
  respondendo ACK normalmente (o ACK só confirma que a mensagem chegou,
  não que o comando teve efeito físico). Sintoma no usuário: "girou nada"
  mesmo com todos os `SET_PAN_TILT` sendo confirmados via protocolo.
  Corrigido movendo a inicialização pra um método `iniciar()` chamado
  dentro de `setup()` (mesmo padrão já usado por `motores.iniciar()`,
  `encoders.iniciar()`, `radar.iniciar()`, etc. - `CommandExecutor` era o
  único que fugia desse padrão).
- **Descoberta física no caminho:** depois da correção de firmware, o
  servo ainda não girava - causa era energia mesmo (fonte dedicada dos
  servos não estava ligada). Depois de conectada, confirmado visualmente
  que pan (pino 10) e tilt (pino 11) giram fisicamente de verdade -
  primeira validação física real do Cap 10 s.2 / Cap 8 s.8 nesse projeto.
- **Achado aberto, não resolvido ainda - suspeita de brownout do Mega:**
  comandos `SET_PAN_TILT` falham em receber ACK de forma intermitente e
  não-determinística (às vezes no 1º comando da sessão, às vezes no 5º),
  e logo depois até `RETURN_STATUS` simples fica sem resposta por vários
  segundos - padrão típico de o Mega resetar (bootloader + `setup()` de
  novo) no meio da comunicação. Só acontece em comandos que de fato
  chamam `Servo::write()` - nunca em `WHO_ARE_YOU`/`RETURN_STATUS`.
  Descartado RAM (instrumentei `RETURN_STATUS` com `ram_livre_bytes` via
  `__brkval`/`__heap_start` - ver `memoriaLivre()` em `main.cpp`, ainda
  marcado como diagnóstico temporário; RAM livre estava em ~5.3KB de 8KB
  no momento da falha, longe de esgotar). Descartado também tamanho do
  buffer de quadro (`DecodificadorQuadro::CAPACIDADE = 320` bytes, folga
  grande para o payload do `SET_PAN_TILT`). Descartado não ser
  proporcional ao ângulo do movimento (pan=2°/5° passaram, pan=-5° logo
  em seguida falhou). Usuário confirmou GND comum entre a fonte dos
  servos e o Mega (descarta essa causa) e fonte de 3A (descarta
  insuficiência de corrente em regime). Hipótese que sobra: pico de
  corrente **transiente** (milissegundos) no instante em que o servo
  começa a girar, rápido demais para a malha de regulação da fonte
  reagir, mesmo numa fonte de 3A nominal - fix recomendado é um
  capacitor eletrolítico (470-1000 µF, ≥10V) entre +5V e GND bem perto
  dos servos, pra absorver esse pico localmente. Usuário vai instalar o
  capacitor e retestar com `tools/testar_pan_tilt.py` (script novo,
  criado hoje, fala direto com o Hardware Core pela serial sem precisar
  subir o `motion_core` inteiro - mostra `uptime_ms` e `ram_livre_bytes`
  a cada passo pra distinguir reset real de perda de pacote pontual).
- **Confirmado nesta sessão:** kiosk do avatar (Fofão) já sobe sozinho no
  Notebook depois de um reboot - autologin + autostart configurados
  ontem (Fase 8) validados de verdade pela primeira vez.
- **Causa raiz real encontrada (não era elétrica):** capacitor instalado
  pelo usuário não mudou nada - falha idêntica, mesmo comando, mesmo
  `uptime_ms`/`ram_livre_bytes`, o que já indicava algo determinístico no
  firmware, não brownout. Testado desligando temporariamente o
  `HEARTBEAT`/`TELEMETRY` periódico do `loop()` (comentados) - com isso,
  os 10 comandos do teste passaram 100% das vezes, uptime subindo sem
  interrupção. Causa confirmada: `Serial.write()` do `HEARTBEAT`
  (a cada 1s) e do `TELEMETRY` (a cada 500ms) é **bloqueante** sempre que
  a mensagem excede o buffer de transmissão padrão do core AVR (64
  bytes) - enquanto bloqueado, o `loop()` não volta a checar
  `Serial.available()`, e o buffer de **recepção** (também só 64 bytes
  por padrão) pode estourar se um `COMMAND` estiver chegando ao mesmo
  tempo, corrompendo/perdendo bytes do quadro (descartado silenciosamente
  por CRC inválido - Cap 14 s.5) - dai o ACK nunca chegava.
- **Fix aplicado:** `firmware/hardware_core/platformio.ini` ganhou
  `build_flags` definindo `SERIAL_RX_BUFFER_SIZE=256` e
  `SERIAL_TX_BUFFER_SIZE=256` (padrão do core é 64) - RAM do Mega (8KB)
  tem sobra de sobra pros +384 bytes. `HEARTBEAT`/`TELEMETRY` religados
  no `loop()`. Instrumentação temporária de RAM (`ram_livre_bytes` no
  `RETURN_STATUS`, `memoriaLivre()`) removida do firmware depois de servir
  pra descartar a hipótese de esgotamento de heap. **Reconfirmado com o
  firmware final: 10/10 comandos do `tools/testar_pan_tilt.py` passando,
  uptime contínuo sem nenhum reset.**
- **Pan e tilt validados fisicamente de ponta a ponta, de forma
  confiável** - primeira vez no projeto. Fecha o critério de "pronto" do
  `SET_PAN_TILT` (Cap 8 s.8) no nível de hardware, faltando só conectar
  o Vision Core de verdade a esses comandos (hoje só testado
  manualmente via `tools/testar_pan_tilt.py`).
- **Renomeação incompleta corrigida:** o sed de "ORION X" -> "Fofão" de
  mais cedo hoje só cobriu `*.md`/`*.yaml`/`*.py` e deixou passar HTML/
  JS/CSS da interface web e do avatar, `tools/orion-avatar.service`/
  `.desktop`, e principalmente `config/prompt_sistema.txt` - o robô se
  apresentaria como "ORION X" numa conversa de voz de verdade (Fase 6).
  Corrigido e sincronizado também no Notebook via scp.
- **Achado real no Notebook - gap na config de "nunca dormir" (Fase 6):**
  usuário reportou a tela do kiosk entrando em descanso sozinha.
  Systemd sleep/suspend/hibernate seguiam mascarados, logind com
  `HandleLidSwitch=ignore` etc., DPMS desligado - tudo isso conferido e
  correto. O que faltava: o protetor de tela **nativo do X11** (`xset
  s`), mecanismo separado do DPMS, seguia no padrão (`timeout: 600`,
  `prefer blanking: yes`) - apagava a tela depois de 10min parado
  mesmo com o resto certo. Corrigido com `xset s off; xset s noblank;
  xset -dpms` embutido no `Exec=` do `.desktop` do kiosk, pra rodar
  toda vez que o autostart sobe (sobrevive a reboot).
- **Próximo passo:** conectar o Vision Core (rastreamento de rosto) ao
  pan/tilt de verdade; motores de passo ainda não foram montados
  fisicamente (fica para outra sessão).
- **Commitado:** histórico reconstruído em 11 commits (Fase 0 a 8 + 2
  commits de hoje) - ver `git log --oneline`.

## 2026-07-18 (continuação - motores e ultrassons chegaram fisicamente)

Usuário conectou fisicamente hoje: os 2 motores de passo (NEMA17 via
TB6600), o ultrassom traseiro (pinos 26/27, antes reservado) e o
ultrassom frontal remontado no servo do radar (pino 9, antes fixo sem
servo). Primeira vez testando esses três com hardware real.

- **`pins.h` atualizado:** `ULTRASSOM_TRAS_TRIG/ECHO` e `SERVO_RADAR`
  movidos de RESERVADO para CONFIRMADO. Comentário desatualizado do
  `RadarManager` (dizia "ultrassom fixo, sem servo") corrigido.
- **Ultrassom traseiro ganhou código de verdade:** antes só tinha os
  pinos reservados em `pins.h`, nenhum manager lia eles. Adicionado
  `SensorUltrassonico ultrassomTraseiro` global em `main.cpp`,
  `TelemetryManager` ganhou o campo `distancia_traseira_cm`/`_valida`
  (novo parametro no construtor).
- **Servo do radar (pino 9):** confirmado fisicamente girando (o
  `RadarManager::iniciar()` ja chamava `_servo.attach()` dentro de um
  metodo proprio chamado do `setup()`, entao nao tinha o mesmo bug de
  construtor global do pan/tilt de mais cedo).
- **Bug real corrigido - `ENABLE_ATIVO_EM_BAIXO`:** motores completamente
  mudos/parados (nem vibravam) com o valor original (`true`, ativo em
  baixo). Invertido para `false` - os TB6600 desta montagem só habilitam
  com o pino em HIGH. Comentário do código ja antecipava essa
  possibilidade (`motor_manager.h`).
- **Achado NÃO resolvido - ultrassons sem eco:** mesmo com energia e
  GND comum confirmados pelo usuário, `echo_frontal_ja_visto_alto` e
  `echo_traseiro_ja_visto_alto` (flag nova em `SensorUltrassonico`,
  fica `true` para sempre no primeiro ECHO valido desde o boot -
  substituiu um diagnostico anterior por amostragem via
  `RETURN_STATUS` que era pouco confiavel, dado o pulso de ECHO poder
  ser mais curto que o intervalo de amostragem) continuam `false`
  depois de mais de 8s (100+ tentativas de trigger) em ambos os
  sensores. TRIG/ECHO conferidos contra os pinos certos (22/23
  frontal, 26/27 traseiro) pelo usuário. Causa raiz não encontrada -
  precisa de multímetro (medir se o TRIG realmente pulsa no pino certo,
  se ECHO tem 5V quando deveria, GND de verdade comum).
- **Achado NÃO resolvido - motores não giram, apesar de tudo checado:**
  depois do fix do `ENABLE_ATIVO_EM_BAIXO`, motor chegou a "quase girar"
  uma vez (zumbido, sem completar o giro - padrão de corrente
  insuficiente), mas depois de ajustar a corrente (usuário mexeu de
  0.5A nominal do motor até 1.2A, depois para ~0.7A) parou de fazer até
  isso. Checklist completo tentado sem sucesso: ENABLE nas duas
  polaridades E flutuando (pino sem `pinMode(OUTPUT)`), corrente em
  varios valores, STEP/DIR reencostados, fios da bobina invertidos,
  GND comum de PUL-/DIR-/EN- confirmado, testado inclusive com um
  motor+cabo de uma CNC (conhecido bom) no lugar do NEMA17 - mesmo
  resultado. **Prova definitiva de que não é código:** com
  `tools/testar_pan_tilt.py`-style telemetria, confirmado que o
  firmware gera e conta os 200 passos certos (`passos_esquerda`/
  `passos_direita` = 200 para um `MOVE_DISTANCE` de 5cm). Escrito
  tambem um sketch minimo (`/tmp/.../motor_diag/`, fora do repo,
  descartavel) que pulsa STEP/DIR/ENABLE via `digitalWrite()` puro, sem
  nenhum codigo do protocolo, alternando a polaridade do ENABLE
  sozinho a cada 10s - motor tambem nao reagiu. Ou seja: testado em
  dois niveis de software (protocolo completo + GPIO cru) e varias
  frentes de hardware, sem sucesso. Suspeita mais forte agora: driver
  TB6600 com defeito, ou rompimento fisico de fiacao entre Mega e
  driver nao visivel sem multimetro (continuidade fio a fio). Pausado
  aqui - precisa de instrumento de medicao pra continuar.
- **Susto a parte:** o servo de tilt teve um episodio de movimento
  erratico ("ficou maluco") no meio da sessão, provavelmente ruído
  induzido pelo manuseio da fiação dos motores por perto. Mega
  confirmado vivo e saudável durante o episódio (RETURN_STATUS normal).
  Retestado depois com `tools/testar_pan_tilt.py` e voltou ao normal
  (10/10 comandos ok) - não reaconteceu, mas fica registrado caso volte.
- **Pendências físicas para retomar:** (1) multímetro para depurar os
  ultrassons (TRIG pulsando? ECHO com 5V?) e os motores (continuidade
  Mega→driver→motor, tensão real no driver); (2) considerar testar os
  TB6600 isolados (sem o Mega, com um gerador de pulso simples ou
  outro microcontrolador) para confirmar se o defeito é mesmo no
  driver.
- **Não commitado ainda** (mudanças de hoje: `pins.h`, `radar_manager.h`,
  `telemetry_manager.h`, `main.cpp`, `motor_manager.h`,
  `sensor_ultrassonico.h`).

## 2026-07-18 (fechamento da sessão - pinagem dos ultrassons confirmada correta, ainda sem sinal)

- **Motores - decisão do usuário:** depois do checklist completo sem
  sucesso (ver entrada anterior), incluindo testar com motor+cabo+driver
  **inteiros** de uma CNC (conjunto conhecido bom, mesmo resultado: nada),
  usuário decidiu não perseguir mais o NEMA17/TB6600 por agora e já
  comprou motores DC de 2 fios + ponte H. Arquitetura de controle é
  bem diferente (PWM de velocidade + pino de direção, sem STEP/DIR/
  micropasso) - `motor_manager.h` vai precisar ser reescrito do zero
  quando o hardware novo chegar. Até lá, o `MotorManager` atual
  (stepper) fica como está no código, sem mais tentativas de debug.
- **Ultrassons - pinagem confirmada correta, causa raiz ainda não
  encontrada:** usuário conferiu fio a fio e confirmou explicitamente:
  frontal TRIG=22/ECHO=23, traseiro TRIG=26/ECHO=27 - exatamente o que
  `pins.h` espera (bateu certinho depois de descartar duas hipóteses
  no caminho: um sensor temporariamente achado em pinos 24/25, que são
  do DHT/LED no nosso firmware - acabou não sendo o caso real; e uma
  inversão TRIG/ECHO no traseiro, também descartada na checagem final).
  Mesmo com a pinagem 100% batendo com o código, `echo_frontal_ja_visto_alto`
  e `echo_traseiro_ja_visto_alto` continuam `false` - nenhum pulso de
  ECHO chegou nos pinos do Mega em nenhum teste do dia. Mesma
  conclusão dos motores: sem multímetro pra medir tensão real nos
  pinos (TRIG realmente pulsando? VCC do sensor realmente em 5V? ECHO
  do sensor emitindo algo?), não dá pra achar a causa exata só
  verificando fiação visualmente.
- **Confirmado funcionando de verdade hoje:** pan, tilt e o servo do
  radar (varredura física do `SCAN_FRONT`) - os três reconfirmados
  pelo usuário no fim da sessão.
- **Resumo do estado físico ao final do dia:**
  - ✅ Pan/tilt (servos, pinos 10/11)
  - ✅ Servo do radar (pino 9) - varredura física confirmada
  - ⚠️ Ultrassom frontal e traseiro (pinos 22/23, 26/27) - pinagem
    correta, sem sinal, precisa de multímetro
  - ⚠️ Motores de passo (NEMA17/TB6600) - pausado, indo para motor DC
    + ponte H (hardware novo a caminho)
- **Próxima sessão:** (1) quando os motores DC + ponte H chegarem,
  escrever `motor_manager.h` novo pra essa arquitetura; (2) com
  multímetro em mãos, medir TRIG/VCC/ECHO ponto a ponto nos dois
  ultrassons; (3) considerar Vision Core -> pan/tilt de verdade
  (Fase 5 -> hardware), já que pan/tilt está validado.
- **Não commitado ainda.**



## 2026-07-18 (noite - diagnostico de software cravou a causa dos ultrassons: ECHO flutuando)

- **Ferramenta nova:** `tools/testar_ultrassom.py` - monitor ao vivo dos
  dois ultrassons via RETURN_STATUS (distancias, flags de validade e os
  flags `echo_*_ja_visto_alto`), no estilo do `testar_pan_tilt.py`.
  Primeira rodada: mesmo resultado da sessao anterior, nenhum ECHO
  desde o boot em nenhum sensor.
- **Sketch descartavel `ultra_diag`** (scratchpad, fora do repo), em 3
  versoes progressivas, todas com `pulseIn()` bloqueante puro (zero
  codigo do protocolo):
  - v1: ECHO lia HIGH constante *mesmo sem pullup* nos dois sensores;
    `pulseIn` sempre 0. Primeira hipotese: clone do HC-SR04 travado
    com ECHO preso em HIGH.
  - v2 (truque de destravamento - forcar ECHO LOW como OUTPUT por
    10ms): depois disso o pino passou a ler LOW constante. O "HIGH
    constante" da v1 era so a carga do pullup retida no fio - ou seja,
    o pino segue o que a gente grava nele.
  - v3 (prova do fio flutuante): carrega o pino em HIGH -> solta ->
    ainda le 1 apos 50ms; descarrega em LOW -> solta -> le 0. Nos DOIS
    sensores, em todos os ciclos. **Um pino conectado a um sensor vivo
    forcaria o proprio nivel; segurar a carga gravada e assinatura de
    pino eletricamente flutuando.**
- **Conclusao:** os pinos 23 (ECHO frontal) e 27 (ECHO traseiro) do Mega
  NAO tem conexao eletrica com uma saida ativa. Duas causas possiveis:
  (a) os fios de ECHO nao chegam eletricamente ao Mega (jumper ruim/
  trilha interrompida), ou (b) **os sensores estao sem alimentacao** -
  HC-SR04 sem VCC fica com a saida em alta impedancia, que flutua
  igualzinho. Como os DOIS sensores flutuam identico, a causa comum
  mais provavel e alimentacao (ex.: trilho de 5V da protoboard
  interrompido no meio - pegadinha classica - ou fio VCC/GND do trilho
  solto).
- **Nao e codigo, nem pinagem:** confirmado em dois niveis (firmware
  completo + sketch cru) e com a pinagem ja conferida fio a fio na
  sessao anterior.
- **Atencao:** o Mega esta com o sketch de diagnostico gravado agora -
  regravar o firmware real (`pio run -t upload` em
  `firmware/hardware_core/`) depois do conserto fisico.

## 2026-07-18 (noite - Fofao na TV e PRIMEIRA CONVERSA DE VOZ COMPLETA!)

- **Avatar na TV via HDMI do Notebook:** TV detectada como segundo monitor
  (HDMI-2, 1920x1080). `xrandr --output HDMI-2 --primary` + relancamento
  do Firefox kiosk (com `setsid -f`, senao o processo morre junto com a
  sessao SSH) colocou o avatar em tela cheia na TV. Confirmado pelo usuario.
- **Descoberta que destravou a Fase 6: a TV e a saida de audio!** O chip
  HDA do notebook so expoe audio via HDMI (journal da Fase 6) - e agora ha
  uma TV no HDMI ("HDA Intel PCH: Android TV"). Teste com Piper + aplay
  confirmado pelo usuario: "a voz saiu". O headset USB planejado deixou de
  ser bloqueante para a saida.
- **Ferramenta nova `tools/conversar_fofao.py`:** substituto ao vivo do
  preview_avatar - AvatarServer + VoiceCore + AiManager (Ollama) +
  Sintetizador num processo so, mesmo Event Bus: o avatar do kiosk reage
  aos estados reais de voz. Sem Raspberry envolvido (EDR-0018).
- **Indices de audio reconferidos e atualizados em config/orion.yaml**
  (mudam quando USB troca de porta!): saida agora e a TV (indice 2);
  mics candidatos [0, 1, 5]. Seletor escolheu o mic da PC Camera (1).
- **Bug real achado e corrigido - offset DC na captura
  (`captura_audio.py`):** em silencio absoluto o RMS media ~0,25 (quase
  identico ao RMS falando!) - o mic entrega o sinal deslocado de um nivel
  constante. Isso inflava o RMS, enganava a selecao por qualidade e
  degradava o Whisper (janelas viravam '' ou alucinacao tipo "e acalpa-lo
  no inside"). Fix: subtrair a media do trecho gravado. Depois do fix,
  silencio mede rms 0,02-0,03 e fala ~0,16-0,23 - separacao limpa.
- **Wake word na pratica:** o Whisper ouve "Fofao" mas escreve "Fafao" ou
  "furacao" (e ate completa "carreta furacao" sozinho, contexto dos
  palhacos). `conversar_fofao.py` passa um DetectorPalavraAtivacao com as
  variacoes (fofao/fafao/fufao/furacao, com e sem til) - paliativo ate um
  modelo openWakeWord treinado para "Fofao" existir (decisao ja documentada
  em wake_word.py).
- **PRIMEIRA CONVERSA COMPLETA (criterio de pronto da Fase 6 - PARCIAL,
  ver abaixo):** usuario disse "Oi, fofao!" -> wake detectado -> comando
  transcrito -> Ollama respondeu -> Piper falou pela TV, avatar reagindo:
  "Ahah, sim! Sou um robo da marca Carreta Foracao, modelo Fofao..."
  (o proprio llama3.2 entrou na brincadeira da carreta).
- **183 testes passando** (6 de voz revalidados apos o fix do DC).
- **Pendencias:** (1) o criterio completo da Fase 6 inclui "acenda a
  lanterna" acionando LIGHT_ON - precisa do link Notebook->Raspberry->Mega
  de pe (Mission Planner completo); (2) trocar o orion-avatar.service para
  apontar para conversar_fofao.py se o usuario quiser a conversa no boot;
  (3) armadilha aprendida: pkill -f com padrao que aparece no proprio
  comando ssh mata a propria sessao (aconteceu 3x) - padrao seguro e
  "[c]olchete" no inicio E nenhuma ocorrencia literal no resto do comando.

## 2026-07-18 (madrugada - ajustes de conversa ao vivo com a familia testando)

- **"Oi? Pode falar!":** VoiceCore ganhou `frase_ativacao` opcional - sem
  uma confirmacao audivel o usuario nao sabia QUANDO falar e a janela de
  comando gravava silencio (visto ao vivo). Commit 98942ce.
- **Velocidade (queixa real: "demora muito pensando"):** resposta do
  Ollama limitada a 100 tokens + keep_alive de 30min (sem isso a primeira
  pergunta apos ocioso pagava o recarregamento inteiro do llama3.2).
  Commit d309cef.
- **Experimento fracassado, revertido no mesmo commit noturno:** Whisper
  "tiny" na vigia da wake word (para acelerar as janelas de 3s). Rapido,
  porem errava demais com este mic: "Fofão" virou "Loco! Loco!"/"Fofa no"
  e o robo nao acordava. Vigia voltou ao "base" via config
  (`whisper_model_ativacao`), a infra de dois transcritores fica pronta
  para um modelo wake word de verdade no futuro. Commits d309cef/50585ab.
- **Variacoes de wake word acumuladas ao vivo:** fafao, fufao, furacao,
  falfao (grafias reais que o Whisper produziu para "Fofão").
- **Kiosk da TV:** quando o servidor do avatar reinicia, a pagina fica
  "sem conexao" - relancar o firefox resolve (sem auto-reconexao no
  frontend ainda - candidato a melhoria no avatar_server/JS).
- **RAM do notebook monitorada durante a farra toda: ~3,8GiB disponiveis**
  com 2x Whisper base + Ollama + Firefox kiosk + avatar - folga boa.

## 2026-07-18 (fechamento - cerebro novo, wake word fuzzy e conversa validada pela familia)

- **Cerebro trocado: llama3.2:3b -> gemma3:4b** (pedido do usuario: "a
  outra IA estava melhor"). Portugues visivelmente superior, ~3,6GB
  residentes no Ollama (RAM do notebook fica com ~1,6-1,7GiB disponiveis
  - apertado mas estavel; alerta automatico armado em <700MiB durante a
  sessao). Respostas do gemma3 vem com emoji/markdown - `limpar_para_fala`
  em conversar_fofao.py tira antes do Piper. Commit e047c81.
- **Wake word virou fuzzy** (DetectorFuzzy em conversar_fofao.py):
  distancia de edicao <=2 de "fofao" (sem acentos) acorda o robo, alem
  das variacoes exatas (japao entra por lista, distancia 3). Lista fixa
  tinha virado enxugar gelo: cada teste ao vivo produzia grafia nova
  (fafao, falfao, japao, vovao...). Falso positivo conhecido e aceito:
  "botao". Commit 54067e9.
- **Servico de boot agora e o modo conversa:** orion-avatar.service roda
  conversar_fofao.py (nao mais o preview de eventos falsos), e o kiosk
  espera o servidor responder (curl em loop) antes de abrir o Firefox -
  fim da pagina de erro congelada quando o Firefox abria antes da hora.
  Commit 2a84e59. Licao: o "sem conexao" persistente era isso; o
  avatar.js sempre soube reconectar sozinho.
- **Corrida boba que confundiu o teste:** um `systemctl restart` meu caiu
  exatamente entre o "Voce disse" e a resposta - o usuario viu o robo
  "nao pensar nem responder". Diagnostico: nenhum crash, so o meu
  restart. Regra nova: nao reiniciar o servico com conversa em andamento.
- **Validacao final pela familia:** wake fuzzy acordou, comando garbled
  ("Quero que a presidenta camou!") e o gemma3 respondeu na hora
  "Desculpe, nao entendi. Pode repetir?" - em segundos (keep_alive
  pagando dividendos). Usuario: "excelente".
- **Proximos passos naturais:** (1) headset/caixa USB com mic melhor
  continua sendo o upgrade mais impactante para a transcricao dos
  comandos; (2) modelo openWakeWord treinado para "Fofao" aposentaria o
  fuzzy; (3) ligar o Mission Planner ao Motion Core para "acenda a
  lanterna" fechar o criterio completo da Fase 6.

## 2026-07-19 (teste ultrassom apos religar no 5V do Arduino)

- Usuario trocou a alimentacao dos HC-SR04: fonte externa 5V -> 5V do
  proprio Arduino. Teste com tools/testar_ultrassom.py (37 leituras):
  comunicacao ok, uptime continuo (sem brownout com a carga nova), mas
  **eco zero nos dois sensores** - `echo_*_ja_visto_alto` ficou "nao" o
  tempo todo, frontal e traseiro.
- Conclusao: o problema nao era a fonte. Sensor mudo total aponta para
  causa fisica: TRIG/ECHO trocados (sintoma exato), VCC/GND sem contato
  ou jumper dupont ruim. Proximo passo: conferir ordem VCC-Trig-Echo-GND
  no corpo do sensor e medir com multimetro (TRIG pulsando = ~0,1-0,8V
  medio oscilante, nao 5V fixo).
- Isolamento hardware x firmware: gravado sketch minimo descartavel
  (pulseIn bloqueante, scratchpad) no Mega. Resultado: SEM ECO nos dois
  sensores, pino ECHO parado em baixo. Firmware do ORION inocentado -
  defeito e fisico. Os dois mudos ao mesmo tempo sugere causa comum:
  linha de 5V/GND compartilhada (trilho de protoboard, jumper) ou os dois
  modulos queimados pela fonte externa anterior. Sketch de teste segue
  gravado no Mega durante a depuracao fisica; regravar firmware oficial
  (cd firmware/hardware_core && pio run -t upload) ao terminar.
- Escaner de pinos (sketch descartavel: pulsa trigger em cada pino 22..31
  e escuta todos os outros): achou sensor VIVO em TRIG=24/ECHO=25,
  medindo distancia estavel (~273cm) - sensor bom, so que fora da posicao
  planejada (22/23). Pino 22 esta preso em ALTO no repouso (100/100
  amostras) - comportamento tipico da linha de dados do DHT (idle alto
  por pull-up), nao de ultrassom; suspeita de DHT e ultrassom trocados de
  coluna no conector duplo. Segundo ultrassom nao respondeu em nenhum par
  22..31: trig dele provavelmente solto/fora dessa faixa, ou modulo morto.
  Licao: "5V chegando" + eco zero nao significa modulo queimado - conferir
  mapeamento real dos pinos por software antes de condenar hardware.
- Identificacao ao vivo: com a mao do usuario na frente do sensor FRONTAL,
  o par 24/25 caiu de ~273cm para 2-15cm e voltou ao tirar. Confirmado:
  frontal = vivo e saudavel, plugado uma coluna deslocado (24/25 em vez
  de 22/23). Pino 22 preso em alto = provavel fio do DHT (deveria estar
  no 24) - DHT e ultrassom frontal aparentemente trocados de coluna.
  Traseiro segue invisivel no escaner 22..53 (trig solto ou modulo sem
  5V/morto) - pendente inspecao fisica.
- Usuario reposicionou os fios (frontal 24/25 -> 22/23, DHT -> 24, e
  ajustou o traseiro): sketch minimo confirmou OS DOIS sensores medindo
  (frontal ~36cm estavel, traseiro reagindo a mao). O traseiro nunca
  esteve morto - era so posicao de fio tambem.
- Firmware oficial regravado no Mega. testar_ultrassom.py mostrava
  "eco desde boot: SIM" mas distancia "---": o script pedia
  distancia_*_cm ao RETURN_STATUS, que nunca teve esses campos - eles so
  existem na TELEMETRY (Cap 5). Corrigido o tool: assina
  comm.mensagem.telemetry no Event Bus (inicia bus.iniciar() em task) e
  imprime a distancia do ultimo quadro TELEMETRY; RETURN_STATUS segue
  para uptime (vigia de reset) e flags de diagnostico.
- Validacao final com firmware oficial: frontal ~37cm estavel, traseiro
  acompanhando a mao (3-250cm), flags SIM nos dois. pytest tests/unit:
  183 passed, 7 skipped. Pendente: remover os flags DIAGNOSTICO
  TEMPORARIO (echo_*_ja_visto_alto) do firmware quando nao forem mais
  uteis.

## 2026-07-19 (motores de passo giraram pela primeira vez)

- Usuario chateado: motores de passo nao mexiam. Diagnostico guiado com
  sketch descartavel (scratchpad/teste_motores): ciclo de 6 fases variando
  ENABLE (pino 6) e pulsando cada motor (1600 passos @ 400Hz) para separar
  polaridade de ENABLE x micropasso x fiacao.
- Causa raiz: PUL-/DIR- dos TB6600 nunca foram ligados ao GND do Mega -
  sem circuito de retorno, o optoacoplador nunca ve pulso. Na CNC antiga
  do usuario "so iam dois fios" (PUL+/DIR+) porque a placa fechava o
  retorno por baixo. ENA segue solto = sempre habilitado (igual CNC).
  Fonte de potencia 12-24V isolada pelo opto - nao precisa de GND comum.
- Apos ligar PUL-/DIR- ao GND do Mega: "GIROU" (usuario, ao vivo).
  Pendente anotar: quais fases giraram, voltas por rajada (revela o
  micropasso das chaves DIP) e se o ENABLE do firmware tem efeito real.
- Usuario confirmou: "ficou otimo" - motores girando. Firmware oficial
  regravado no Mega e validado (handshake ok, ultrassons medindo).
  Pendencias para a proxima sessao de bancada: (1) contar voltas por
  rajada para confirmar o micropasso das DIP e calibrar passos/metro no
  config; (2) decidir o destino do ENABLE (pino 6): esta solto nos
  drivers (= sempre habilitado), entao o firmware aciona um pino sem
  efeito - ou ligar ENA de verdade (economia de energia/aquecimento em
  idle) ou documentar que e ignorado; (3) teste de bancada completo do
  Cap 10 (MOVE_DISTANCE, TURN, STOP via protocolo).
- Decisao do usuario: ENA fica SOLTO (drivers sempre habilitados, como na
  CNC dele). Documentado em pins.h (fiacao real dos TB6600: PUL+/DIR+ nos
  pinos 2-5, PUL-/DIR- no GND do Mega, ENA nao conectado). Firmware
  recompilado ok - sem mudanca de comportamento, so documentacao.

## 2026-07-19 (MARCO: cadeia completa viva - Notebook -> Pi -> Arduino)

- Fase 7 iniciada pela integracao. Descoberta: python -m motion_core ja
  existia completo (memoria da sessao anterior estava desatualizada).
  Primeira execucao real no Pi: TCP 5757 ouvindo, Arduino confirmado via
  WHO_ARE_YOU no serial, webui 8080 no ar, SSD ausente tolerado.
- BUG REAL achado pela sonda de integracao (cliente minimo no Notebook):
  toda resposta do Arduino encaminhada pelo Pi ao Notebook era NACKada
  ("checksum invalido"). Causa: o firmware C++ nao reproduz a serializacao
  JSON canonica do Python; o link serial ignora o checksum de mensagem de
  proposito (confia no CRC16 do enquadramento), mas o roteador repassava o
  checksum original para o enlace TCP, que valida. Correcao: _encaminhar
  reassina o checksum ao rotear (service.py) + teste de regressao
  (test_roteamento_reassina_checksum_de_mensagem_do_firmware). 10/10 no
  test_service.py; fix sincronizado ao Notebook via scp.
- CADEIA COMPLETA VALIDADA: do Notebook, WHO_ARE_YOU ao Pi (ok),
  WHO_ARE_YOU ao Arduino atraves do Pi (ok), RETURN_STATUS pela cadeia
  inteira (ok). Bonus: estado veio OBSTACLE_DETECTED - a seguranca
  reativa do Mega (Cap 18 camada 1) esta ativa sozinha na bancada, com o
  ultrassom frontal vendo obstaculo proximo. imu_conectado=False (MPU
  ainda nao ligado fisicamente - esperado).
- Motion Core segue rodando em background no Pi (webui em
  http://10.20.20.185:8080).

## 2026-07-19 (VAD: escuta do Fofao ficou ~20x mais barata)

- Medicao no Notebook: load 4.0 cravado em 4 nucleos, conversar_fofao a
  165% de CPU continuo - o Whisper de vigilancia transcrevia TODA janela
  de 3s, 24h, mesmo silencio (e alucinava: "E ai? E ai?"). RAM ok
  (gemma3 descarrega quando ocioso).
- Implementado portao VAD por energia com piso de ruido adaptativo
  (src/orion/voice/vad.py, DetectorAtividadeSonora): percentil 20 do
  historico de RMS = piso; som so passa se > fator (2.5x) o piso, com
  rms_minimo absoluto 0.003. Config em voice.vad no orion.yaml (Cap 17).
  VoiceCore ganhou detector_atividade opcional; janela silenciosa nem
  chega ao Whisper. Testes: test_voice_vad.py (4) + regressao de custo no
  test_voice_core.py; 10/10 no Notebook.
- Resultado apos restart: 8.3% de CPU instantanea (era 165%), zero
  transcricoes em silencio (eram ~26/80s), load caindo (4.0 -> 2.9).
  Ruido ambiente da sala: RMS ~0.02 - piso adaptativo aprendeu sozinho.
- PENDENTE VALIDAR: usuario dizer "Fofao" ao vivo para confirmar que o
  portao nao deixou o robo surdo (se nao acordar: baixar
  fator_acima_do_ruido para ~1.8 no orion.yaml, ou desabilitar).
- Decisao de arquitetura mantida: nada migrou do Notebook para o Pi (IA
  nao cabe no Pi 4GB; mics/audio sao fisicos do Notebook; Cap 9/EDR-0019
  preservados) - em vez de mudar ONDE roda, baixamos QUANTO custa.
  openWakeWord treinado para "Fofao" segue como evolucao futura.

## 2026-07-19 (banco de dados vivo no SSD + primeira memoria do Fofao)

- Descoberta: o Pi ja BOOTA do SSD de 500GB (sda2 = raiz, 412G livres) -
  nao ha cartao SD; a exigencia do EDR-0019 (banco no SSD) e satisfeita
  criando /mnt/ssd/orion no proprio filesystem. Criados orion/ e backups/.
- Fio solto achado no "ligar tudo": a PonteMemoria (Fase 3, testada em
  test_memory_bridge.py) nunca era registrada pelo __main__.py do Motion
  Core - com o banco aberto, os comandos memory.* do Notebook morriam sem
  resposta. Corrigido: PonteMemoria(memory_api, comm).registrar(bus)
  quando o banco abre.
- Motion Core reiniciado: Migracao 1 aplicada, orion.db em WAL no SSD,
  ponte ativa. Validacao ponta a ponta do Notebook via TCP:
  memory.remember (id=1, conhecimento/primeiro_dia_de_vida) ->
  memory.recall (voltou identica) -> memory.stats (conhecimento: 1).
  A primeira memoria persistente da vida do Fofao.
- pytest: 184 passed, 8 skipped. __main__.py sincronizado ao Notebook.
- Motion Core virou servico permanente: tools/orion-motion.service
  (systemd --user, Restart=always) + loginctl enable-linger -> sobe no
  boot do Pi e renasce se cair; nao depende mais de sessao aberta.
  Verificado apos a troca: WHO_ARE_YOU no motion_core e no hardware_core
  (via serial) e memory.stats ok pelo TCP.
- Pegadinha anotada para melhorar: _ao_conectar_notebook registra QUALQUER
  cliente TCP como "mission_core" (nome fixo) - um cliente com outro
  nome_local nunca recebe resposta (rota inexistente) e o heartbeat vaza
  para ele. Melhorar junto com autenticacao de origem (Cap 14 s.9).
- Pegadinha de shell que custou um restart: pkill -f "a\|b" NAO e
  alternacao (ERE usa "|" sem escape) - o processo antigo sobreviveu
  segurando as portas 5757/8080 e o servico novo crashava em loop.

## 2026-07-19 (Mission Planner ligado - Fase 6 fechada na integracao)

- conversar_fofao.py agora e o Mission Core completo: conecta via TCP no
  Motion Core no boot (tolerando ausencia, Cap 6 s.8), monta o
  MissionPlanner com enviar_comando_hardware (comm.send ao hardware_core)
  e MemoryClient - predefinidos e hora resolvidos sem IA, conversa livre
  vai ao gemma3, interacoes registradas em "conversas" no banco do SSD.
- Teste de texto do criterio da Fase 6 (sem falar, do Notebook):
  "acenda a lanterna" -> LIGHT_ON ACKado pelo Mega real pela cadeia;
  "que horas sao" -> resposta direta; conversa livre -> IA; 4 linhas
  gravadas em conversas. O proprio teste achou 2 bugs de regex:
  "apague" (subjuntivo) caia na IA, e "desligue a luz" LIGARIA a
  lanterna ("ligue a luz" e substring; LIGHT_ON era testado primeiro).
  Corrigidos (\w* nas conjugacoes + OFF antes de ON) com 2 regressoes.
- Servico reiniciado no Notebook: "Motion Core conectado - comandos de
  hardware e memoria ATIVOS". 16/16 testes de planner+voz no Notebook.
- PLANO atualizado: Fase 6 "[~] QUASE" (falta so a validacao FALADA,
  aguardando mic USB sex 24/07); Fase 4 "[~] DESBLOQUEADO" (bancada
  parcial de 2026-07-19 registrada).

## 2026-07-19 (git remote Pi->Notebook - fim do scp artesanal)

- Antes de mexer: checksum de TODOS os arquivos rastreados nas duas
  maquinas. Divergencias eram uniformemente o Notebook desatualizado
  (rename "ORION X"->"Fofao" nunca sincronizado + mudancas de hoje) -
  nada exclusivo do Notebook a preservar. Ancestral comum confirmado
  (Notebook = commit raiz 819456c).
- Setup: remote "notebook" no Pi (jproma23@10.20.20.195:orion-os),
  push fast-forward 819456c..f726278, git reset --hard no Notebook e
  receive.denyCurrentBranch=updateInstead - de agora em diante,
  sincronizar = "git push notebook" no Pi (o worktree do Notebook
  atualiza sozinho se estiver limpo). Historico do Notebook saiu da
  Fase 0 e agora espelha o Pi.
- Faxina: tests/unit/conftest.py orfao no Notebook (duplicata antiga do
  FakeTransporte de tests/conftest.py) removido; suite completa re-rodada
  no Notebook apos o reset.

## 2026-07-19 (faxina do firmware - flags de diagnostico removidos)

- Removidos os DIAGNOSTICO TEMPORARIO echo_*_ja_visto_alto de
  sensor_ultrassonico.h, radar_manager.h e do RETURN_STATUS (main.cpp) -
  cumpriram a missao na cacada dos fios. testar_ultrassom.py atualizado
  (sem os flags; nota nova: parar orion-motion.service antes de rodar,
  senao a serial esta ocupada - com o servico de pe, conferir distancias
  em http://<pi>:8080/estado).
- Mega regravado (servico pausado durante o upload e religado).
  Verificacao pela cadeia TCP: RETURN_STATUS sem os flags; webui mostrando
  distancia_frontal_cm ~15 e OBSTACLE_DETECTED coerente com a bancada.
  Detalhe de arquitetura confirmado de passagem: TELEMETRY e endereada ao
  motion_core e consumida la (nao e repassada ao mission_core) - clientes
  TCP nao a veem; a webui e o retrato dela.

## 2026-07-19 (Fase 7 conferida e validada - navegacao pronta menos autocalibracao)

- Levantamento: NavigationCore (HOLD/MANUAL/GOTO/PATROL completos, FOLLOW
  minimo deliberado) e FusaoSensores ja existiam com 27 testes unitarios +
  8 de integracao (sim) - todos verdes no Pi.
- Validacao ao vivo do fluxo de missao SEM movimento: do Notebook,
  comm.publish("navigation.comando", MANUAL/STOP) -> modo MANUAL na webui
  (STOP entregue ao Mega); depois HOLD -> modo HOLD. Cadeia de missao
  Notebook->Pi->Arduino operante.
- PLANO Fase 7 atualizado: 4 de 5 entregaveis concluidos; pendencias
  fisicas: autocalibracao (Cap 12 s.9), encoders/IMU nao montados, FOLLOW
  completo quando a visao rodar junto ao vivo.

## 2026-07-19 (Behavior Core + resiliencia do link Notebook<->Pi)

- EDR-0020: Behavior Core ("maestro"/consciencia) - arbitragem de
  prioridade sobre o Event Bus, roda no Pi (revisao: no estavel sobrevive
  a crash do Notebook). NAO adota ROS (so multitarefa exige coordenacao,
  nao middleware; ROS futuro so p/ SLAM). Esqueleto BehaviorCore +
  Comportamento + 3 testes.
- Guardiao de RAM do Notebook (behavior/guardiao_ram.py): Notebook publica
  diagnostic.notebook_health (/proc/meminfo) a cada 10s; guardiao no Pi
  pede behavior.reduzir_carga_ia + alerta abaixo do limiar, com histerese.
  3 testes.
- Arduino port-scan: CH340 re-enumerou p/ ttyUSB1 e o servico (so ttyUSB0)
  falhava; _conectar_arduino agora varre ttyUSB0/1/ACM0. Reconectou.
- BUG FOUNDATIONAL consertado: o Notebook NAO reconectava ao Pi apos um
  restart do Pi - ficava orfao p/ sempre (reporter so imprimia "Falha ao
  propagar"). Novo supervisor de link em conversar_fofao.py: conecta no
  inicio e reconecta em comm.module_lost (retenta 5s). Comprovado ao vivo:
  restart do Pi -> "Link caiu -> reconectando" -> reconectado em 2s.
- So DEPOIS disso o guardiao de RAM disparou ponta a ponta ("RAM do
  Notebook critica: 5110 MB < 5608"). Era o link fragil que mascarava.
- Licao: verificar features de rede ao vivo exige o link resiliente
  primeiro; meus restarts do Pi orfanavam o Notebook e davam falso
  negativo no guardiao.

## 2026-07-19 (primeiros comportamentos plugados no maestro)

- Comportamento ganhou gancho _maestro/_reavaliar (o BehaviorCore o
  preenche em registrar) para a subclasse acordar o maestro quando o
  gatilho muda.
- Dois comportamentos concretos (comportamentos.py): Repouso (prio 10,
  base) e VigilanciaObstaculo (prio 100, dispara com motion.status ==
  OBSTACLE_DETECTED, sinal real do Mega ja no Event Bus do Pi). 2 testes
  (repouso assume sem obstaculo; obstaculo preempta e libera).
- Maestro ligado no motion_core/__main__.py e ativo no robo: ao subir,
  assumiu 'repouso' (hardware IDLE). Preempcao por obstaculo coberta por
  teste; ao vivo reage quando o Mega reportar. Notebook reconectou
  sozinho apos o restart do Pi (fix da reconexao confirmado de novo).

## 2026-07-19 (Atender plugado - maestro reage ao "Fofão")

- Atender (prio 80) no maestro: dono chama "Fofão" -> robo para (HOLD) e
  atende ate a resposta terminar. Notebook encaminha voice.wake_detected
  e voice.response_finished ao Pi (comm.publish local=False, novo, evita
  eco). 3 testes: atender preempta repouso + HOLD; obstaculo (100) vence
  atender (80). Maestro no ar com repouso+atender+vigilancia; assumiu
  repouso no boot (hardware IDLE). Prova falada (dizer "Fofão") fica para
  o usuario testar no robo. Nota: journalctl --user segue flaky ("No
  journal files"); usar `systemctl --user status -n N` para ver o log.

## 2026-07-19 (família cadastrada - Fofão conhece os donos)

- Cadastrados os 3 moradores por foto (tools/cadastrar_de_fotos.py, fotos
  baixadas no Pi -> scp pro Notebook -> face_recognition no Notebook ->
  memory.remember via TCP com embedding base64): João Paulo (dono, id=1,
  2 fotos), Ana (dono, id=2, 4 fotos), Bruno (morador, id=3, 3 fotos; 1
  foto sem rosto detectavel, ignorada). Embeddings de 1024 bytes (128
  float64) confirmados na tabela pessoas do SSD. A ponte binaria base64 do
  bridge.py funcionou ponta a ponta.
- Gmail: conector achou o rascunho "fotos" mas NAO baixa anexo (sem
  permissao + sem get_attachment) - plano B (fotos direto no Pi) resolveu.
- face_recognition NAO esta no Pi (so no Notebook, Cap 8) - fotos precisam
  passar pelo Notebook para virar embedding.
- Base do Modo Sentinela pronta: rosto que nao casar com os 3 = estranho.

## 2026-07-19 (Sentinela de visão - rosto desconhecido dispara alerta)

- Transporte binario de recall (a metade que faltava): bridge
  _codificar_binarios embrulha BLOB em base64 no resultado; MemoryClient
  _decodificar_binarios desembrulha. O Notebook agora LE os embeddings da
  familia do banco. Round-trip testado.
- SentinelaVisao (src/orion/vision/sentinela_visao.py, roda no Notebook):
  carrega os conhecidos, olha um frame a cada intervalo_s; rosto que nao
  casa -> salva foto (data/sentinela) + sentinela.alerta {tipo:pessoa} ->
  encaminhado ao Pi -> Vigilia (maestro) assume. Config
  behavior.sentinela_visao. Ligado no conversar_fofao, tolera camera/link
  ausentes. Ao vivo: "3 rostos conhecidos carregados" + "Sentinela de
  visao ativa". Prova do estranho fica para o usuario (por alguem de fora
  na frente da camera).
- Detector de barulho (VAD) NAO plugado ainda: RMS sozinho nao separa
  barulho de fala (dispararia em conversa/TV) e o VoiceCore ja e dono do
  mic. So faz sentido em "modo ausente" - decisao pendente do usuario.

## 2026-07-19 (Teste do MPU6050 - sensor ok, offset de 9.3 graus)

- **Ferramenta nova:** `tools/testar_imu.py` - monitor ao vivo do MPU6050,
  mesmo molde do testar_ultrassom.py: inclinacao/impacto vem da TELEMETRY
  (`comm.mensagem.telemetry`), RETURN_STATUS so serve para o uptime (vigia
  de reset). Firmware ja publicava tudo (telemetry_manager.h), nao precisou
  mexer no Arduino.
- Rodado no Mega real (motion.service parado): handshake ok
  (0.1.0-fase2), 37 leituras em 25s, `imu_conectado` true, zero reset,
  zero falha de I2C. **I2C nos pinos 20/21 confirmado funcionando.**
- **Achado:** parado, a inclinacao le 9.3 graus constante com ruido de so
  +-0.5 grau. Ruido baixo = sensor saudavel; os 9.3 sao offset fixo
  (chassi torto ou MPU colado torto). Como LIMITE_INCLINACAO_GRAUS = 20,
  o robo trava com ~11 graus reais de um lado e aguenta ~29 do outro -
  assimetria a corrigir (calibrar zero no iniciar() ou reposicionar o
  modulo). Falta ainda validar o flag de impacto (tapinha no chassi).
- Nota de execucao: rodar os tools com `.venv/bin/python -u` - sem o `-u`
  o stdout fica preso no buffer quando a saida e canalizada.
- **Correcao do offset (mesma sessao):** decidido com o usuario que ele
  vai colar o MPU reto e o firmware zera os vetores em cima da posicao
  boa. Implementado em `imu_manager.h`: em vez de subtrair um escalar
  (errado - a inclinacao e um angulo sem direcao, subtrair furaria pro
  lado contrario), guarda o VETOR de gravidade lido nivelado e mede o
  angulo entre a leitura atual e essa referencia (produto escalar +
  acos). Referencia default (0,0,1) = comportamento antigo.
- Calibracao persiste na EEPROM (endereco 0, magico 0x0110, com sanidade
  de norma contra EEPROM corrompida), entao nao precisa regravar firmware
  se o modulo sair do lugar. Comando novo `CALIBRATE_IMU` (main.cpp) e
  flag `imu_calibrado` na TELEMETRY e no RETURN_STATUS.
- `tools/testar_imu.py --calibrar` dispara a calibracao e sai.
- **Efeito colateral util:** depois de calibrado, leitura longe de 0 com o
  robo nivelado = o modulo se mexeu. Diagnostico de graca (ideia do usuario).
- Compilado e GRAVADO no Mega: RAM 33.4%, flash 16.2%. Falta o usuario
  colar o modulo reto e rodar o --calibrar.
- Nota de ambiente: o PlatformIO NAO esta no PATH nem no .venv - fica em
  `/home/jproma23/.platformio-venv/bin/pio`.
- **CALIBRADO ao vivo:** usuario colou o modulo, rodado `--calibrar`, EEPROM
  gravada. Verificacao logo depois: **9.3 graus -> 0.3 graus**, ruido de
  +-0.2 grau. Zero no lugar, limite de 20 graus agora simetrico nos dois
  lados. Servico orion-motion religado.
- **PENDENCIA aberta:** durante a verificacao (20s, ~14 leituras) UMA
  resposta de RETURN_STATUS se perdeu (timeout de 3s). Nao foi reset - o
  uptime seguiu subindo (4463ms -> 8584ms), so a resposta sumiu. Suspeita:
  mesmo ACK perdido de 2026-07-18 que motivou SERIAL_*_BUFFER_SIZE=256 no
  platformio.ini - os buffers reduziram mas nao eliminaram. Investigar
  antes da Fase 4 fechar; frequencia observada ~1 em 14.
- Flag de impacto ainda NAO validado (precisa de tapinha no chassi).
- **Recalibrado:** usuario mexeu no modulo durante o teste de tapa (era a
  explicacao dos 6.8 graus - o diagnostico "de graca" funcionou na
  pratica, primeira vez que ele pegou um deslocamento real). Ajeitado e
  recalibrado: de volta a 0.4 grau.
- **Bug encontrado e corrigido - flag de impacto invisivel:** o teste de
  tapa deu ZERO deteccoes. Causa nao era o tapa: `_impactoDetectado` era
  INSTANTANEO. Tranco dura ~10-20ms, IMU e lido a cada 50ms e a TELEMETRY
  so sai a cada 500ms - o evento quase sempre morria entre amostras.
  Enfraquecia tambem o SafetyManager.
- Correcao: janela de tempo (`JANELA_IMPACTO_MS = 1000`, maior que os
  500ms da telemetria) em vez de "zerar na leitura". Zerar-na-leitura foi
  DESCARTADO de proposito: SafetyManager le todo loop e telemetria a cada
  500ms - quem lesse primeiro roubaria o evento do outro e a telemetria
  voltaria a nao ver nada. A janela apaga sozinha, sem disputa.
- Regravado e testado ao vivo: impacto apareceu como SIM em 2 quadros
  seguidos (a janela de 1s cobrindo dois quadros de 500ms, exatamente o
  desenhado). **Flag de impacto validado.**
- Confirmado tambem que a calibracao da EEPROM SOBREVIVE a regravacao do
  firmware (leituras seguiram em 0.3-0.6 grau depois do upload).
- **PENDENCIA nova:** so 1 evento detectado em ~3-4 tapas. O mecanismo
  agora funciona, mas o limiar de 2.5 G parece alto para tapa de mao.
  Baixar exige cuidado: 2.5 G dispara parada de emergencia, e limiar baixo
  demais gera falso positivo passando por soleira/desnivel. Calibrar com o
  robo andando de verdade, nao na bancada.

## 2026-07-19 (CAUSA RAIZ das respostas perdidas: o DHT fantasma)

- Investigada a pendencia da resposta perdida. **Causa raiz achada e
  corrigida** - e nao era o que o platformio.ini dizia.
- **O mecanismo:** a biblioteca Adafruit DHT faz bit-banging no pino COM
  AS INTERRUPCOES DESLIGADAS. Com elas desligadas a ISR que enche o buffer
  serial nao roda; a 115200 baud chegam ~57 bytes em 5ms e o hardware do
  ATmega so segura 2-3. O resto se perde e corrompe o quadro em transito.
  **Buffer maior nao ajuda** - o problema e a ISR parada, nao o tamanho.
  Isso explica por que SERIAL_*_BUFFER_SIZE=256 (18/07) reduziu mas nunca
  eliminou o ACK perdido: tratamos sintoma, nao causa.
- **Agravante:** o DHT NAO ESTA INSTALADO (usuario confirmou). Sensor
  ausente e PIOR que presente - em vez de responder em ~5ms, a leitura
  gasta o timeout inteiro esperando um pino que nunca muda.
- **Como foi provado (metodo vale registrar):** as falhas pareciam
  aleatorias. Script probe_dht.py rodou 200 requisicoes e imprimiu o
  uptime de cada falha MODULO 5000ms (o intervalo do DHT). Deu 4757, 4763
  e 4760 - 6ms de espalhamento. Aleatorio nao faz isso. Hipotese confirmada.
- **Correcao:** chave `ORION_DHT_INSTALADO` (dht_manager.h), em 0 enquanto
  o sensor nao existe - nao encosta no pino. Quando instalar, trocar para
  1 e regravar. Fica tambem uma auto-deteccao (3 falhas seguidas ->
  `_ausente`, retentativa a cada 60s) para o caso do sensor cair depois.
- **Resultado medido:** antes 3 falhas/200 (1,5%). Depois **0 falhas/200**.
  Teste longo de 3000 requisicoes em andamento.
- Impacto real disso: ~1,5% de TODO comando se perdia silenciosamente,
  incluindo STOP. Nao era ruido estatistico, era o freio falhando 1 em 66.

## 2026-07-19 (Bateria + integracao cognitiva: modelo de mundo)

- **Sensores termicos aproveitados (`src/orion/diagnostics/sensores_termicos.py`):**
  le /sys/class/thermal, existe no Pi e no Notebook, sem dependencia nova.
  Medido: Pi cpu-thermal 66,7 C | Notebook x86_pkg_temp 50,0 / pch 46,0 /
  acpitz 27,8 C. Cobre o item "temperatura da CPU/SoC" do Cap 16.
  **NAO substitui o DHT** - esses sensores medem o proprio chip, nao o
  ambiente, e nenhum mede umidade. Sao complementares.
- **Notebook NAO TEM BATERIA:** /sys/class/power_supply so lista ADP1 (a
  fonte, online=1); upower nao enumera nenhum device de bateria. O plano
  de ler saude da bateria pelo notebook nao tinha como funcionar.
- **Alimentacao definida pelo usuario:** pack de parafusadeira 18V (max)
  alimenta motores e notebook; buck reduz para 5V para Pi e sensores. Ele
  ja tem os modulos buck e buck-booster. (Alertei sobre o notebook em
  barramento nao regulado; ele disse que os modulos resolvem.)
- **Bateria no firmware (`bateria_manager.h`):** divisor 100k/27k em A0.
  18V -> 3,83V no pino; 21V -> 4,46V (ainda seguro); consumo 0,14mA.
  Limiares 5S Li-ion: aviso 16,5V (3,3V/celula), critica 15,0V (3,0V).
  Media de 8 amostras. analogRead NAO desliga interrupcoes (ao contrario
  do DHT), entao pode rodar no loop sem risco para a serial.
  Chave `ORION_BATERIA_INSTALADA = 0` enquanto o divisor nao existe -
  mesma licao do DHT: pino solto flutua e reportaria tensao inventada.
  **ATENCAO na montagem:** exige GND comum Mega<->bateria (os motores sao
  opto-isolados e nao precisavam; a medicao de tensao precisa).
- Campos novos na TELEMETRY: bateria_lida / bateria_tensao_v /
  bateria_percent / bateria_nivel. Firmware gravado no Mega.
- **Integracao cognitiva - usuario quer as 3 camadas** (modelo de mundo,
  memoria na decisao, IA decidindo). Elas se empilham; comecei pela base.
- **Modelo de mundo (`motion_core/behavior/estado_do_mundo.py`):** funde
  telemetria, estado do hardware, visao, sentinela e voz num RetratoDoMundo
  unico que os comportamentos consultam, em vez de cada um assinar seus
  proprios eventos e ver so um pedaco.
  **Decisao de projeto central:** todo dado tem PRAZO DE VALIDADE. Vencido
  vira None ("nao sei"), nunca continua sendo servido como atual - senao,
  com o cabo solto, o quadro diria "obstaculo a 34cm, tudo ok" com dado de
  5 minutos atras e o maestro decidiria em cima de ficcao. Respeita tambem
  os flags *_valida do firmware.
- 12 testes novos (tests/unit/test_estado_do_mundo.py), com foco no
  envelhecimento. Suite completa: 208 passed, 8 skipped.
- **PENDENCIA:** teste longo anterior (3000 req) nao concluiu - o timeout
  cortou antes do resumo. Zero falhas impressas em ~15min, mas sem numero
  final. Refazendo sobre o firmware definitivo com progresso visivel.

## 2026-07-19 (Serial 3000/3000; comparacao de modelos de IA)

- **Teste longo concluido: 3000 requisicoes, 3000 ok, ZERO falhas**, sobre
  o firmware definitivo. Antes da correcao do DHT: 1,5%. Causa raiz das
  respostas perdidas esta enterrada.
- **Comparacao de modelos (bench com prompts reais do Fofao):**
  | modelo | RAM | conversa | decisao | alucinou? |
  |---|---|---|---|---|
  | gemma3:1b | 880 MB | 10,5s | 10,6s | SIM - "vi a Ana passar" |
  | llama3.2:3b | 2,6 GB | 34,5s | 25,6s | SIM (pior) - inventou registro as 14h30 |
  | gemma3:4b | 2,9 GB | 20,2s | 51,5s | NAO - disse que nao teve noticia |
- **Contraria a percepcao inicial:** o gemma3:4b foi o mais RAPIDO na
  conversa e o UNICO honesto. O llama3.2:3b ("o que funcionava") foi o
  mais lento e o que mais inventou. O 1b tambem errou o nome do
  comportamento ("vigilancia" em vez de "vigilia").
- **Quantizacao: nao ha ganho a buscar.** Os tres ja sao Q4_K_M (padrao do
  ollama). Ja estamos em 4-bit.
- **Diagnostico de fundo:** a alucinacao NAO e problema de modelo. O prompt
  do bench perguntava da Ana sem dar dado nenhum sobre ela - sem
  informacao qualquer modelo chuta. Conserto e grounding (entregar os
  fatos + instruir a dizer "nao sei"), nao troca de modelo.
- Decisao com o usuario: construir o CONTEXTO primeiro, remedir os modelos
  depois - com os fatos na mao a tarefa muda e os numeros de hoje (feitos
  sem contexto) deixam de valer. qwen2.5:3b baixado para entrar no
  proximo comparativo.
- **PENDENCIA (achado lateral):** ninguem assina `behavior.reduzir_carga_ia`.
  O GuardiaoRamNotebook publica o pedido de alivio e o evento morre - so
  os testes escutam. O guardiao nao protege nada hoje.

## 2026-07-19 (Grounding: a correcao real da alucinacao + troca para gemma3:1b)

- **`src/orion/mission/grounding.py`:** transforma o que o robo sabe num
  bloco de fatos para o prompt. A ideia central nao e listar o que se sabe,
  e **dizer em voz alta o que NAO se sabe** - campo ausente vira
  "nao sei (sem registro)", e lista de observacoes vazia vira "nao tenho
  NENHUM registro hoje... devo dizer isso". Silencio convida invencao.
- **Resultado medido - mesma pergunta que fez todos mentirem:**
  | modelo | SEM contexto | COM contexto | tempo |
  |---|---|---|---|
  | gemma3:1b | "Sim, vi! A Ana passou" | "Nao vi." | 16,5s |
  | llama3.2:3b | inventou registro "as 14h30" | "Nao vi nada, Joao Paulo!" | 41,6s |
  | qwen2.5:3b | (nao testado sem) | "Nao vejo registros de ninguem hoje." | 43,2s |
  | gemma3:4b | "nao tive noticia" (ok) | "Nao vi, Joao Paulo." | 48,8s |
  **Os QUATRO pararam de inventar.** Confirma: nunca foi problema de
  modelo, era falta de contexto (o usuario tinha razao).
- **TROCA DE MODELO: gemma3:4b -> gemma3:1b.** So virou possivel por causa
  do grounding: com os fatos prontos a IA nao deduz, so le e formula.
  Ganho: 880MB em vez de 2,9GB (RAM do notebook e apertada) e o mais
  rapido dos quatro com contexto (16,5s contra 48,8s do 4b).
  Config comentada com o historico e com o aviso: **se voltar a inventar,
  o suspeito e o grounding ter parado de entregar os fatos, nao o modelo.**
- `config/prompt_sistema.txt` ganhou a regra anti-invencao explicita.
- `AiManager._montar_prompt_sistema` agora usa o grounding e injeta o bloco
  SEMPRE - inclusive vazio, porque contexto vazio ("nao tenho registro de
  nada") e justamente o que impede a invencao.
- 11 testes novos (tests/unit/test_grounding.py). Suite: 219 passed, 8 skipped.
- **RESSALVA:** validado em UM caso (recusar o que nao sabe). Falta testar
  o caso oposto - quando ele TEM o dado e precisa usa-lo direito. O 1b
  errou o nome de comportamento antes ("vigilancia" em vez de "vigilia"),
  entao a camada 3 (IA decidindo) precisa de validacao de saida.

## 2026-07-19 (Camada 3: IA aconselha, regra manda)

- **Saida estruturada testada no gemma3:1b** (ollama 0.32.1 suporta schema
  JSON com `enum`): 4/4 respostas com nome de comportamento VALIDO. O erro
  "vigilancia" (que nao existe) acabou - a gramatica nao permite gerar
  valor fora da lista.
- **MAS o teste revelou coisa pior: as ESCOLHAS estao erradas.** O 1b
  escolheu `repouso` em 3 de 4 situacoes:
  | situacao | escolheu | correto |
  |---|---|---|
  | obstaculo a 34cm | repouso | vigilancia_obstaculo |
  | rosto desconhecido 03:00 | vigilancia_obstaculo | vigilia |
  | Ana chamou "Fofao" | repouso | atender |
  | tudo calmo | repouso | ok |
  **Schema garante resposta valida, nao resposta certa.**
- **Consequencia de projeto:** a IA virou CONSELHEIRA, nao decisora
  (`src/orion/mission/conselheiro_comportamento.py`):
  - seguranca ativa -> a IA NEM E CONSULTADA (regra determinística vence);
  - `COMPORTAMENTOS_DE_SEGURANCA` nunca entram na lista oferecida a ela -
    entrar em seguranca e condicao fisica medida, nao opiniao;
  - toda resposta e validada de novo em Python (cinto e suspensorio, caso
    o schema falhe);
  - timeout de 8s: conselho atrasado nao serve, o maestro nao espera.
  No pior caso (IA burra/lenta/fora do ar) o robo se comporta exatamente
  como se comportava sem ela.
- Import de `ollama` ficou preguicoso (dentro do __init__): a lib so existe
  no Notebook, e o modulo precisa ser importavel no Pi e nos testes.
- 8 testes novos, todos sobre "a IA nao pode atrapalhar". Suite: 227 passed.
- **Modelos:** removidos llama3.2:3b e qwen2.5:3b (superados). MANTIDO o
  gemma3:4b - com o julgamento fraco do 1b, ele e o candidato natural para
  a camada de decisao. Disco tem 176GB livres, nao ha pressao.
- **Quantizar o 4b: NAO.** Ja e Q4_K_M. Descer para Q3/Q2 exige gerar com
  llama.cpp, economiza ~1GB de disco que sobra, e degrada justamente a
  qualidade de julgamento que e o unico motivo de manter o 4b. Modelo
  parado tambem nao ocupa RAM - so o carregado ocupa.
- **PENDENCIA:** conselheiro criado e testado, mas ainda NAO plugado no
  BehaviorCore. Falta a ponte Pi<->Notebook (o maestro roda no Pi, a IA no
  Notebook) e decidir em que situacoes vale consultar.

## 2026-07-19 (Conselheiro plugado no maestro - a IA opina, a regra manda)

- **Problema descoberto ao plugar:** os 4 comportamentos existentes sao
  todos disparados por condicao concreta (voz, alerta, obstaculo). Quando
  nenhum quer rodar sobra so `repouso` - pedir conselho com UMA opcao e
  decoracao. Conselho so significa algo se houver escolha real.
- **`Ronda` (prio 20), primeiro comportamento DISCRICIONARIO:** dar uma
  olhada em volta quando nao ha nada acontecendo. So olha (pan/tilt +
  SCAN_FRONT), **NAO anda** - movimento de rodas por iniciativa da IA seria
  arriscado demais no primeiro passo; olhar e reversivel.
- **Ponte Pi<->Notebook** (`motion_core/behavior/ponte_conselho.py` +
  `src/orion/mission/conselho_protocolo.py`): pedido e resposta casados por
  `id`, senao resposta atrasada de pedido antigo seria confundida com a
  atual. Timeout de 6s; None significa "decida pela regra", nao erro.
- **Correcao de arquitetura no meio do caminho:** eu tinha posto o
  atendente em `motion_core/` e feito o Notebook importar de la. Errado -
  eles tem deploy separado e a dependencia so anda num sentido
  (`motion_core` importa `orion`). Protocolo + atendente mudaram para
  `src/orion/mission/conselho_protocolo.py`.
- **Gancho no maestro** (`_pode_consultar_ia`): a IA so e consultada na
  AMBIGUIDADE - ninguem de gatilho concreto no controle e nenhum
  discricionario ja querendo rodar. Resposta que nao seja o nome exato de
  um discricionario registrado e ignorada (inclui "repouso", que e um
  conselho legitimo de nao fazer nada).
- 9 testes novos, todos sobre "a IA nao pode atrapalhar": Notebook mudo,
  lento, explodindo, resposta invalida, resposta atrasada de pedido velho,
  e maestro sem ponte nenhuma. Suite: **236 passed, 8 skipped**.
- **VALIDADO NO SERVICO REAL:** orion-motion reiniciado, log mostra
  `comportamentos: vigilancia_obstaculo, atender, vigilia, ronda, repouso`
  e depois `sem conselho em 6.0s - seguindo pela regra` (o Notebook nao
  esta rodando conversar_fofao). Degradacao graciosa confirmada em campo,
  nao so em teste.
- Limpeza: a lista de comportamentos no log era escrita a mao e ja estava
  desatualizada; agora vem de `maestro.nomes_registrados`.
- **PENDENCIA:** falta ver o conselho ACEITO ao vivo - precisa do
  conversar_fofao rodando no Notebook ao mesmo tempo. E o julgamento do
  gemma3:1b e fraco (escolheu repouso com obstaculo a 34cm), entao vale
  medir se ele acerta a escolha ronda-vs-repouso ou se o 4b e necessario
  aqui.

## 2026-07-19 (Teste ao vivo do conselheiro: DESLIGADO, e por que)

Sequencia de falhas encontradas ao ligar o conselheiro ao vivo, na ordem:

1. **Bug meu:** em conversar_fofao.py escrevi `event_bus` onde a variavel
   local se chama `bus`. O processo subia mudo e nunca conectava no Pi.
2. **Evento nao atravessa o link sozinho** (Cap 14 s.7): quem quer mandar
   um evento ao outro no precisa reencaminhar com `comm.publish(...,
   local=False)`. Faltava nos dois lados - o Pi perguntava no vazio.
   Adicionado: Pi repassa TOPICO_PEDIDO, Notebook repassa TOPICO_RESPOSTA.
3. **Tempestade de requisicoes (causada por mim):** o maestro repergunta a
   cada timeout (6s) e a inferencia levava >8s. Pedidos empilhavam mais
   rapido que as respostas; o link TCP caia e reconectava em laco, com
   **37 conexoes vazadas** na porta 5757. Corrigido com
   `INTERVALO_CONSULTA_IA_S = 120` (ociosidade nao e urgencia).
4. **Timeouts invertidos:** Pi esperava 6s, conselheiro do Notebook 8s - o
   Pi desistia antes de a resposta poder existir. Agora Pi 25s /
   conselheiro 20s (o de fora tem que ser maior que o de dentro).
5. **A causa que nao tem conserto por parametro:** cada consulta derrubava
   o link. Padrao no log: heartbeat perdido -> IA estoura o timeout ->
   "Link com o Motion Core caiu". A inferencia do gemma3:1b satura os
   nucleos do notebook por ~20s; o processo Python e preterido, os
   heartbeats atrasam e o Pi declara o link morto. **asyncio.to_thread
   protege o event loop de BLOQUEAR, mas nao de FALTA DE CPU.**

- **DECISAO: conselheiro DESLIGADO por padrao** (`behavior.conselho_ia.
  habilitado: false`). Entregava zero conselhos (a inferencia estourava o
  timeout) e custava uma queda de link a cada 2 minutos - pior que nao ter.
  Codigo pronto e testado (236 passed); religar e trocar a flag, depois de
  resolver a causa. Verificado apos desligar: log em silencio, link estavel.
- **Heartbeat e o no do problema:** intervalo 1s, limite 3 perdidos = link
  morto em 3s. Tolerancia curta demais para uma maquina que roda inferencia
  local. Subir exige config POR LINK - tolerante com o Notebook, rigida com
  o Mega (onde detectar falha rapido importa de verdade). Trabalho real,
  nao troca de numero.
- **Proposta do usuario (mover visao e voz para o Pi, notebook so com IA):
  NAO recomendada.** O problema nao e onde as coisas rodam, e CPU saturada
  convivendo com laco sensivel a atraso. Mover para o Pi faz saturar a
  maquina MAIS FRACA (4GB vs 8GB) e MAIS CRITICA - a que fala com o Arduino
  e roda a seguranca tatica. Notebook engasgado e chato; Pi engasgado e o
  robo perdendo o corpo. O caminho e o inverso: o forte fica com o peso, o
  Pi passa a TOLERAR o notebook engasgar.
- **PENDENCIA (achado lateral, importante):** vazamento de conexoes TCP -
  a reconexao do Notebook abre socket novo sem fechar o antigo (37 vivas no
  pico, 4 depois de estabilizar). Investigar o supervisor de link.

## 2026-07-19 (Vazamento de conexoes TCP: causa e conserto)

- **Duas fugas, nao uma.** `ComunicacaoService.adicionar_link` so
  sobrescrevia `self._links[nome_peer]`:
  1. o transporte antigo NUNCA era fechado -> socket vazado;
  2. `_tarefas_recepcao` era uma LISTA e so crescia -> a tarefa antiga
     seguia rodando sobre o socket velho.
- **Por que vazava de verdade (e nao so em teoria):** o link e declarado
  morto por HEARTBEAT ATRASADO, nao por socket fechado. Quando o Notebook
  engasgava de CPU (inferencia da IA), o socket continuava perfeitamente
  vivo - cada "reconexao" deixava mais uma conexao ESTABLISHED para tras.
  O comentario no conversar_fofao.py afirmava que o antigo era
  "descartado"; era descartado do dicionario, nunca fechado. Comentario
  corrigido para nao mentir de novo.
- **Conserto:** `_tarefas_recepcao` virou dict por peer e
  `_descartar_link_anterior()` cancela a tarefa e fecha o transporte antes
  de registrar o novo. Fechamento vai em tarefa propria porque
  `adicionar_link` e sincrono e `fechar()` e assincrono.
- **4 testes de regressao** (tests/unit/test_service_reconexao.py), com
  transporte falso que e GERADOR assincrono (como os de verdade). Validados
  dos dois lados: reintroduzi o bug de proposito, os 4 falharam; restaurei,
  os 4 passaram. Teste de regressao que nao falha sem o conserto nao vale.
  Um deles cobre o risco de o conserto ser agressivo demais: fechar o link
  antigo de um peer NAO pode derrubar os outros (Arduino intacto).
- **Provado em campo:** 5 ciclos de restart do Motion Core forcando
  reconexao - contagem de sockets oscilou entre 0 e 1, sem acumular
  (antes: 37 no pico). Log do Notebook agora mostra "Link anterior com
  'motion_core' descartado (socket e tarefa fechados)" a cada reconexao.
- Suite: **240 passed, 8 skipped**.

## 2026-07-19 (Heartbeat por enlace: paciencia com o Notebook, rigor com o Mega)

- **Problema:** um unico `heartbeats_lost_threshold: 3` governava TODOS os
  enlaces = link morto apos 3s sem heartbeat. Curto demais para o Notebook
  (que trava a CPU dezenas de segundos rodando IA local sem estar
  quebrado), mas afrouxar o global afrouxaria tambem o enlace com o
  Arduino, que e o caminho da seguranca reativa.
- **Conserto:** limite POR MODULO.
  - `_EstadoModuloMonitorado.limite_proprio` (None = herda o global);
  - `HealthMonitor.timeout_de(nome)` substitui o `timeout_s` fixo em
    `modulos_com_heartbeat_perdido`;
  - `MonitorHeartbeat.monitorar(..., heartbeats_perdidos_limite=...)`;
  - config `communication.heartbeats_lost_threshold_por_link`.
- **Valores:** mission_core 45 / motion_core 45 / hardware_core no padrao 3.
  O Arduino fica curto DE PROPOSITO - descobrir tarde que o Mega sumiu e
  pior que um falso alarme (Cap 18).
- **Descoberta durante o teste ao vivo (a parte nao obvia):** com apenas
  `mission_core: 45`, o PI parou de derrubar o link - mas o NOTEBOOK
  continuava derrubando o dele. Motivo: a maquina afogada nao so ATRASA os
  proprios heartbeats, ela tambem deixa de LER os que chegam. O Notebook
  concluia que o Pi tinha morrido sendo que o culpado era ele mesmo.
  **Tolerancia precisa valer nos DOIS lados** - dai `motion_core: 45`.
- 8 testes novos (tests/unit/test_heartbeat_por_link.py), incluindo o
  cenario exato do bug (pausa de 20s: o rigido cai, o tolerante nao), que
  tolerancia nao e imortalidade (46s derruba o tolerante tambem), e que dar
  paciencia a um enlace NAO afrouxa os outros. Suite: **248 passed**.
- **Validado ao vivo, em duas etapas:**
  1. CPU do notebook travada 15s -> link sobreviveu (antes, 3s matavam);
  2. conselheiro religado temporariamente: a consulta estourou o timeout de
     25s e **o link NAO caiu** - a cascata original (consulta -> heartbeat
     perdido -> link morto -> reconexao) esta eliminada. Log do notebook
     confirma: "Heartbeat de 'motion_core': tolerancia propria de 45
     perdidos (45s)".
  3. Conselheiro desligado de volta; 2 minutos de observacao: ZERO perdas
     de heartbeat nos dois lados, 1 conexao estavel.
- **O conselheiro continua desligado e sem entregar conselho** - a
  inferencia ainda estoura o timeout de 20s. Mas agora o custo dele e zero
  em vez de derrubar o link. As duas coisas eram problemas separados.
- **PENDENCIA (pequena):** no boot do Notebook aparece um "Heartbeat
  perdido: modulo='motion_core'" antes do primeiro heartbeat chegar -
  falso positivo de partida, seguido de reconexao imediata. Inofensivo,
  mas sujo: o monitor devia dar carencia inicial ao modulo recem-registrado.

## 2026-07-19 (Falso positivo de heartbeat no boot)

- **Causa (nao era o que eu supus):** a perda aparecia IMEDIATAMENTE apos o
  registro, com 45s de tolerancia configurados - logo nao vinha do caminho
  do timeout. Vinha do outro: `iniciar()` chama `enviar_heartbeat` desde a
  primeira volta, mas no boot o laco comeca ANTES de o supervisor TCP abrir
  o link. `_resolver_link` levanta "sem rota", e o codigo tratava isso como
  PERDA. **"Ainda nao conectei" e "perdi a conexao" sao estados
  diferentes** - o codigo confundia os dois.
- **Conserto:** `_ja_estabelecidos` (peers com quem a comunicacao ja
  funcionou pelo menos uma vez, por envio OU por recebimento).
  `_marcar_perdido` ignora quem nunca conectou; o nivel do log de falha ao
  enviar acompanha (debug antes do primeiro sucesso, warning depois).
  Efeito colateral bom: para de disparar reconexao para um link que ja
  estava sendo aberto.
- **Teste antigo precisou mudar** (`test_falha_ao_enviar_heartbeat_tambem_
  gera_comm_module_lost`): ele simulava "link que morreu" simplesmente NAO
  registrando o link - usando "sem rota" como atalho. O atalho virou
  ambiguo, porque "sem rota" e tambem o estado do boot; era exatamente a
  confusao sendo corrigida. Agora o teste usa um transporte que funciona
  uma vez e depois quebra - o cenario que ele sempre disse cobrir. A
  INTENCAO do teste foi preservada, so o setup mudou.
- 4 testes novos (tests/unit/test_heartbeat_boot.py), incluindo o inverso
  (a carencia acaba no primeiro sucesso - nao pode virar mordaca) e que
  RECEBER heartbeat tambem estabelece o peer. Validados dos dois lados:
  bug reintroduzido de proposito -> 2 falharam; restaurado -> passaram.
- Suite: **252 passed, 8 skipped**.
- **Confirmado em campo:** boot do Notebook agora vai direto de
  "tolerancia propria de 45 perdidos (45s)" para "Motion Core conectado",
  sem "Heartbeat perdido" no meio. Pi: zero falsos positivos.

## 2026-07-19 (behavior.reduzir_carga_ia: alguem finalmente escuta)

- **O buraco:** o GuardiaoRamNotebook (no Pi) publicava
  `behavior.reduzir_carga_ia` desde que foi escrito, mas NINGUEM assinava -
  so os testes. A protecao contra travamento por falta de memoria existia
  so no papel. Passou despercebido porque **publicar evento nao falha**: o
  guardiao logava "pedindo alivio de carga" e parecia estar funcionando.
- **Faltavam DUAS pecas, nao uma:**
  1. o evento nao atravessava o link (mesma armadilha do conselheiro) -
     Pi agora repassa `behavior.reduzir_carga_ia` e `diagnostic.recuperado`
     ao Notebook com `local=False`;
  2. ninguem sabia COMO aliviar - criado `src/orion/mission/alivio_carga.py`.
- **O que se sacrifica, nessa ordem:**
  1. modelo de IA sai da RAM (`AiManager.descarregar`, keep_alive=0) - maior
     ganho pelo menor prejuizo: a proxima pergunta recarrega sozinha;
  2. Sentinela de visao pausa (`SentinelaVisao.pausar/retomar`, novo) -
     reconhecimento facial e a parte mais cara; ficar cego alguns minutos e
     melhor que travar a maquina (travada, fica cego do mesmo jeito).
  **A VOZ nao se desliga**: se o dono chamar "Fofao" durante o aperto, o
  robo tem que responder - e a funcao mais basica dele.
- Cada acao e isolada: falha ao descarregar o modelo NAO impede pausar a
  visao (alivio parcial > nenhum). Idempotente: pedido repetido pelo link
  nao alivia duas vezes.
- 9 testes novos (tests/unit/test_alivio_carga.py), incluindo o ciclo
  repetido, recuperacao de outra origem sendo ignorada, e o caso de visao
  desabilitada. Suite: **261 passed, 8 skipped**.
- **VALIDADO AO VIVO** (limiar critico elevado a 5000MB temporariamente
  para forcar o disparo, depois restaurado a 700):
  ```
  PI:   RAM do Notebook critica: 4152 MB livres (< 5000) - pedindo alivio
  NOTE: ALIVIO DE CARGA acionado (RAM livre: 4152 MB)
  NOTE: modelo de IA descarregado da RAM
  NOTE: Sentinela de visao PAUSADA (alivio de carga)
  NOTE: RAM do Notebook recuperada (5237 MB) - retomando carga normal
  NOTE: Sentinela de visao retomada
  ```
  **O alivio liberou ~1,1 GB** (4152 -> 5237 MB). O efeito e mensuravel,
  nao simbolico. Ciclo completo (aliviar + recuperar) confirmado.
- Cuidado que quase repeti: `sentinela` so existe dentro do `if` de visao
  habilitada - inicializada como None antes, senao NameError com visao
  desligada (mesmo tipo de erro do `event_bus`/`bus` de mais cedo).

## 2026-07-19 (Limiar de impacto: instrumentado, nao adivinhado)

- **Nao escolhi um numero novo.** O usuario nao pode ir ate o robo agora, e
  sem medida trocar 2,5 G por outro valor seria so trocar de chute. O que
  dava para fazer sem dado eu fiz - e e mais do que parecia.
- **Instrumentacao (`aceleracao_g` e `pico_g` na TELEMETRY):** antes o
  `impacto_detectado` era um booleano derivado de um limiar invisivel. Agora
  da para VER a aceleracao. `pico_g` guarda o maior valor desde o quadro
  anterior e zera na leitura - o instantaneo quase nunca pega o topo de um
  tranco de 10-20ms (IMU lido a cada 50ms, telemetria a cada 500ms).
- **Medido com o robo parado (93 amostras, 60s):** min 1,01 / mediana 1,01 /
  max 1,02 G. Baseline exatamente na gravidade (confere com a fisica) e
  **ruido de +-0,01 G**. Ou seja: o limiar nao precisa ser alto por causa de
  ruido - 2,5 G esta ~150x acima da faixa de ruido parado. Falta medir o
  piso com o robo ANDANDO (vibracao levanta) e o pico de uma batida real.
- **BUG ENCONTRADO E CORRIGIDO - o acelerometro estava em ±4 G.** Com teto
  de 4 G e limiar em 2,5 G sobrava quase nada de faixa util: uma batida de
  verdade SATURAVA o sensor e o pico saia menor do que foi - esbarrao e
  pancada forte viravam ambos "4 G". **Isso tornava impossivel escolher
  limiar por medida**, entao tinha que ser consertado antes de qualquer
  ajuste. Trocado para ±8 G.
- Custo da troca: ZERO na pratica. Medido depois: baseline 1,01 G e ruido
  +-0,01 G, identicos ao de ±4 G (ADC de 16 bits sobra). A inclinacao
  tambem nao sofre - usa a DIRECAO do vetor normalizado, nao a magnitude.
- **Limiar agora e ajustavel sem regravar firmware:** comando
  `SET_IMPACT_THRESHOLD` + `tools/testar_imu.py --limiar 3.0`, gravado na
  EEPROM em endereco SEPARADO da calibracao do vetor (32, magico 0x0111) -
  ajustar o limiar nao pode apagar a calibracao feita na bancada. Faixa
  aceita 1,05 a 7,5 G (abaixo dispararia com a gravidade parada, acima
  satura). Testado: gravado 3,2 G, resetado o Mega, releu 3,2 G da EEPROM.
  Restaurado ao default 2,5 G - mudar sem medida seria outro chute.
- `limite_impacto_g` tambem vai na telemetria (a interface mostra contra o
  que o pico esta sendo comparado).
- **PENDENCIA (a que realmente fecha o assunto):** com o robo ANDANDO,
  rodar `tools/testar_imu.py` e anotar (a) o pico de vibracao normal
  trafegando, (b) o pico de um esbarrao leve, (c) o pico de uma batida
  real. Escolher o limiar entre (a) e (b), e gravar com --limiar.

## 2026-07-19 — Fechamento da sessão

Começou como "testar o sensor MPU" e virou uma caçada a bugs escondidos.
O MPU estava bom desde o início; o teste é que destravou o resto.

**Consertado (com teste de regressão e validação em campo):**
1. Offset de 9,3° do MPU — vetor de referência na EEPROM (não subtração).
2. Flag de impacto invisível — janela de 1s em vez de instantâneo.
3. **DHT fantasma corrompendo a serial** — bit-banging com interrupções
   desligadas comia ~1,5% de TODO comando, silenciosamente. 3000/3000 ok
   depois. Era a causa real do "ACK perdido" de 18/07.
4. Vazamento de conexões TCP — socket e tarefa não fechados na reconexão.
5. Heartbeat único para todos os enlaces — agora por link.
6. Falso positivo de heartbeat no boot — "ainda não conectei" ≠ "perdi".
7. `behavior.reduzir_carga_ia` que ninguém escutava — proteção contra
   travamento por memória existia só no papel. Libera ~1,1 GB medidos.
8. Acelerômetro em ±4 G saturando em impactos reais — agora ±8 G.

**Construído:**
- Integração cognitiva em 3 camadas: modelo de mundo (com prazo de
  validade em todo dado), grounding (fez 4 modelos pararem de inventar),
  conselheiro de IA (opina, não manda).
- Bateria no firmware (divisor 100k/27k, chave desligada até montar).
- Sensores térmicos de Pi e Notebook.

**Testes: 261 passed, 8 skipped** (eram 208 no início do dia).

**PENDÊNCIAS ABERTAS — em ordem de bloqueio:**

1. **Divisor de tensão da bateria** (físico): montar 100k/27k no A0 com
   GND comum, trocar `ORION_BATERIA_INSTALADA` para 1 e regravar.
2. **Limiar de impacto** (físico): medir com o robô ANDANDO — piso de
   vibração, esbarrão leve, batida real — e gravar com `--limiar`.
3. **DHT** (físico): quando instalar, `ORION_DHT_INSTALADO` para 1.
4. **Conselheiro de IA desligado**: a inferência do gemma3:1b não cabe no
   orçamento de tempo (>20s). Religar exige modelo/hardware mais rápido ou
   rodar a inferência com nice deixando um núcleo livre. Código pronto.
5. **Julgamento do gemma3:1b é fraco** para decisão (escolheu repouso com
   obstáculo a 34cm). Se a camada 3 for religada, avaliar o 4b só para
   isso — foi por essa razão que ele não foi apagado.
6. **Camada 2 da cognição (memória na decisão) não foi construída** — o
   grounding existe, mas quem o alimenta com observações reais ainda não.

**Lição do dia que vale além deste projeto:** dois bugs (o conselheiro e o
alívio de carga) sobreviveram porque **publicar evento não falha**. O
código logava que estava trabalhando e ninguém escutava do outro lado.
Vale desconfiar de todo `publish` cujo consumidor não se consegue apontar.

## 2026-07-20 (Camada 2 da cognicao: o diario - e a licao de testar os DOIS lados)

- **`src/orion/mission/diario.py`:** escuta `vision.person_detected` e
  `sentinela.alerta`, grava na tabela `eventos` (categoria memoria
  "eventos", origem "diario") e le de volta em
  `observacoes_de_hoje()` no formato que o grounding espera. Plugado no
  MissionPlanner (`contexto["observacoes"]`) e no conversar_fofao.
  Ate aqui o bloco de observacoes chegava SEMPRE vazio - o robo era
  honesto por nao ter memoria, nao por ter olhado.
- **Cuidado principal - nao inundar:** `vision.person_detected` dispara a
  cada verificacao. Sem janela de silencio (600s por pessoa) seriam
  centenas de "vi o Joao Paulo" por hora, enchendo o prompt de repeticao -
  a melhor forma de um modelo pequeno perder o que importa. Teto de 8
  observacoes no contexto pelo mesmo motivo.
- 16 testes (tests/unit/test_diario.py).

### O achado que muda a conclusao de ontem

Testei os DOIS lados (com registro e sem) e o 1b **falhou**:

| caso | 1b (enfase em nao inventar) | 1b (enfase em usar fatos) | 4b |
|---|---|---|---|
| A) diario TEM a Ana | "Nao vi" ERRADO | "Sim, as 14:30" ok | ok |
| B) diario VAZIO | "Nao vi" ok | "Sim, a Ana passou" ERRADO | ok |
| C) diario so com BRUNO | "Nao vi" ok | "Sim, as 09:12" ERRADO | "Sim, vi a Ana as 09:12" **ERRADO** |

- **Ontem eu validei o grounding com UM caso** (o de recusa) e declarei
  vitoria. Errado da minha parte: testando o caso oposto, o 1b so troca de
  modo de falha conforme a enfase da instrucao - ele segue o TOM, nao os
  dados. Reescrever o prompt nao conserta.
- **O caso C e o pior e derruba os DOIS modelos:** existe um registro com
  hora, entao respondem "sim, as tal hora" sem conferir DE QUEM e. Atribuir
  a visita do Bruno a Ana e afirmacao falsa sobre a familia.
- **Conclusao de projeto: isso nao e tarefa de modelo de linguagem, e
  consulta a banco.** Perguntas "voce viu o fulano hoje?" passaram a ser
  respondidas DETERMINISTICAMENTE no MissionPlanner
  (`_responder_sobre_quem_viu`), por comparacao de nome, seguindo o padrao
  que ja existia para "que horas sao". A IA fica com conversa livre, que e
  o que ela faz bem. 18 testes (tests/unit/test_planner_diario.py),
  incluindo o caso C que os dois modelos erraram.
- Grounding tambem foi rebalanceado (a regra so punia inventar; agora
  tambem diz que negar o que esta no diario e erro) e separa
  "neste exato momento" de "hoje" - o 1b lia "nao estou vendo ninguem
  agora" e respondia sobre o dia inteiro.
- **Correcao lateral:** `ai_manager` importava `ollama` no topo, o que
  tornava o `mission_planner` inimportavel no Pi e nos testes (nenhum teste
  o importava, por isso ninguem viu). Import preguicoso, igual ao
  conselheiro.
- Suite: **295 passed, 8 skipped**.
- **PENDENCIA:** o modelo continua sendo o gemma3:1b para conversa livre -
  com o grounding ele nao inventa, e a parte factual agora nao passa por
  ele. Mas a validacao "os quatro modelos pararam de inventar" do journal
  de ontem vale so para o caso de RECUSA; nao repetir essa conclusao sem
  testar os dois lados.

---

## 2026-07-20 (blog publico do projeto no GitHub Pages)

- **Repositorio publicado.** Ate hoje o `~/orion-os` so tinha o remote
  `notebook` (via ssh) e o `origin` do GitHub estava vazio, sem nenhum
  branch. Primeiro push do `master` feito hoje: todo o historico foi para
  https://github.com/jproma23/Orion-Os (repo publico).
- **Blog criado.** `tools/build_blog.py` gera um site estatico a partir
  deste proprio journal: quebra o arquivo nas entradas `## <data>`, uma
  entrada = um post, e escreve `docs/index.html` + `docs/posts/*.html`.
  53 posts na primeira geracao.
  - Sem dependencia externa (o conversor de Markdown cobre so o que o
    journal usa: titulos, listas aninhadas, negrito, codigo inline e
    blocos). Nada de Jekyll - por isso o `docs/.nojekyll`.
  - Tema claro/escuro automatico, layout de leitura em coluna unica.
  - O cabecalho das entradas aparece em dois formatos no journal
    (`## data (assunto)` e `## data - assunto`); o parser aceita os dois.
    Sem isso, uma entrada some silenciosamente do indice.
- **GitHub Pages ligado** em `master` + pasta `/docs`. Site no ar:
  https://jproma23.github.io/Orion-Os/
- **Fluxo daqui pra frente:** escrever no journal como sempre, rodar
  `python3 tools/build_blog.py` e commitar. O blog nao e escrito a mao -
  ele e uma projecao do journal, entao nao ha dois lugares para manter.
- **Nota de credencial:** o push exigiu um Personal Access Token
  *fine-grained* com `Contents: Read and write` de fato marcado em
  "Permissions" - marcar so "All repositories" em "Repository access"
  deixa o token somente-leitura, e o erro que aparece e um 403 generico
  no `git push`, que nao diz qual permissao falta.
- **Proximo passo:** seguir na missao de audio (mic USB + caixinhas
  chegando dia 23/07); o blog atualiza junto com o journal.

---

## 2026-07-20 (blog: ilustracoes e as primeiras fotos do hardware)

- **Ilustracoes SVG** dos tres computadores (`docs/assets/`): notebook com o
  rosto do Fofao na tela, Pi 4B com a barra de 40 pinos, Mega com o canto
  chanfrado, mais o diagrama `corrente.svg` mostrando quem fala com quem.
  Sao embutidos no HTML (nao via `<img>`) de proposito: assim herdam
  `currentColor` e acompanham o tema claro/escuro sozinhos.
- **Fotos reais do hardware** publicadas em `docs/assets/fotos/`: a torre
  (webcam no pan/tilt + HC-SR04), o chassi por dentro (Mega, servos,
  driver) e o avatar rodando no notebook.
- **Tratamento das fotos antes de subir** (o script esta no journal porque
  vale repetir): `ImageOps.exif_transpose` para respeitar a orientacao,
  depois copiar para uma imagem nova - assim nenhum metadado e herdado -
  redimensionar para 1400px e salvar em WebP q=82. As originais do celular
  tinham **EXIF com GPS**; o repo e publico, entao isso sai antes do
  commit. 9,8 MB viraram 286 KB.
- **Nao publicar rosto.** As fotos de cadastro facial da familia
  (`~/Downloads/joao paulo|marah|kamall.*`) ficaram de fora por decisao
  explicita: repo publico, e o que entra no git nao sai do historico.
  Regra para as proximas sessoes: foto so do projeto.
- **Proximo passo:** missao de audio (mic USB + caixinhas, 23/07). As fotos
  do resultado entram na galeria do blog.

---

## 2026-07-20 (perfil do GitHub e capas)

- **Perfil do GitHub montado.** Criado o repo `jproma23/jproma23` (branch
  `main`) - o README dele e o que aparece em github.com/jproma23. Texto do
  "sobre mim" escrito pelo proprio Joao Paulo; o README so organizou em
  secoes, tabela do ORION OS e badges.
- **Capa** (o poster do projeto) no topo do perfil e tambem no topo do
  blog, em `docs/assets/capa.webp`.
- **Limite da API, para nao perder tempo numa proxima vez:** um PAT
  consegue criar repo e dar push, mas **nao** troca a foto de perfil (o
  GitHub nao tem endpoint para isso, em nenhum nivel de permissao) nem a
  bio/status (precisa de permissao de perfil, que o token nao tinha).
  Essas tres coisas sao manuais em github.com/settings/profile.
- **Fotos de rosto da familia continuam fora** do repo publico. O poster
  do perfil e foto do proprio dono, em pagina dele - caso diferente.
- **Proximo passo:** missao de audio (23/07). Journal como sempre, depois
  `python3 tools/build_blog.py` e push.
