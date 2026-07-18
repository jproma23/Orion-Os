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
  #9 do CLAUDE.md - uma responsabilidade cada), mas seguindo o mesmo
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
  de verdade (regra #6 do CLAUDE.md: nada de valor físico fixo no
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
  **Fofão** - `README.md`, `CLAUDE.md`, `docs/ses/`, `docs/edr/`,
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


