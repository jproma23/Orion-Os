# ARQUITETURA.md — ORION OS

Regras de arquitetura e convencoes do ORION OS. Todo modulo novo segue o que
está aqui.
**Leia este arquivo antes de qualquer implementação.**

## O que é o projeto

ORION OS é uma plataforma de robótica modular, 100% offline (sem nuvem).
O primeiro robô é o **Fofão**, composto por três computadores em cadeia:

| Unidade | Papel | Hardware | Linguagem |
|---|---|---|---|
| Mission Core | Cérebro: IA, visão computacional, reconhecimento facial, planejamento, voz, avatar | Notebook 8 GB (Linux) | Python 3.11+ |
| Motion Core | Navegação, fusão de sensores, ponte com o Arduino, **memória + banco no SSD 500 GB**, interface web | Raspberry Pi 4 (4 GB) | Python 3.11+ |
| Hardware Core | Tempo real: motores, servos, sensores, segurança reativa | Arduino Mega 2560 | C++ (PlatformIO) |

## Fonte de verdade

A especificação completa está em `docs/ses/` (Capítulos 01 a 20),
alinhada ao documento do TCC pela decisão **EDR-0018**
(`docs/edr/EDR-0018-arquitetura-tcc.md`) e à divisão final de
responsabilidades do **EDR-0019** (`docs/edr/EDR-0019-divisao-final-e-ssd.md`).
**Nenhuma funcionalidade pode violar a especificação.** Em caso de dúvida,
consulte o capítulo correspondente antes de implementar. Mudanças
arquiteturais exigem um novo EDR em `docs/edr/` — o último é o EDR-0019.

Mapa rápido: Cap 5 = protocolo | Cap 6 = Kernel | Cap 7 = Mission | Cap 8 = Vision (Notebook) |
Cap 9 = Voice | Cap 10 = Hardware Core/firmware | Cap 11 = Memória (Raspberry/SSD) | Cap 12 = Motion Core/Navegação (Raspberry) |
Cap 13 = Interface | Cap 14 = Comunicação | Cap 15 = Banco | Cap 16 = Diagnóstico |
Cap 17 = Configuração | Cap 18 = Segurança | Cap 19 = Testes | Cap 20 = Roadmap.

## Regras arquiteturais invioláveis

1. **Toda comunicação entre módulos passa pelo Event Bus.** Nenhum módulo
   importa ou chama outro diretamente. (Cap 6)
2. **Notebook ↔ Arduino: proibido.** A cadeia é Notebook —Ethernet—
   Raspberry —USB Serial— Arduino. Só o Raspberry fala com o Arduino.
   (Cap 3, 5; EDR-0018)
3. **O Arduino nunca executa IA nem navegação**; apenas comandos simples
   (MOVE_FORWARD, MOVE_DISTANCE, TURN_LEFT, STOP, DOCK...) e segurança
   reativa. Toda a lógica de controle dos motores fica encapsulada nele.
   (Cap 10)
4. **A visão computacional roda no Notebook** (`src/orion/vision/`);
   navegação, banco de dados e interface web rodam no Raspberry
   (`motion_core/`), com dados no SSD. (Caps 8, 12, 13, 15)
5. **Nenhum módulo abre o SQLite diretamente** — só via API de memória /
   Database Manager (que roda no Raspberry; o Notebook consulta via
   Ethernet). O banco vive no SSD, nunca no cartão SD. (Cap 11, 15)
   Backup diário com réplica cruzada Raspberry ↔ Notebook.
6. **Nenhum valor fixo no código** — todo parâmetro vem de `config/orion.yaml`
   via Configuration Manager. (Cap 17)
7. **Segurança em 3 camadas**: reativa no Arduino (nunca depende dos
   demais), tática no Raspberry, estratégica no Notebook. Em dúvida,
   parar. (Cap 18)
8. **Todo COMMAND no protocolo exige ACK**; mensagens têm id, versão,
   timestamp e CRC — mesmo formato no TCP e no Serial. (Cap 5, 14)
9. Um módulo = uma responsabilidade. Logs estruturados em tudo.

## Estrutura do repositório

```
orion-os/
├── ARQUITETURA.md                  ← este arquivo
├── PLANO_IMPLEMENTACAO.md     ← fases de desenvolvimento (siga a ordem)
├── config/orion.yaml          ← configuração única (Cap 17)
├── docs/
│   ├── ses/                   ← especificação oficial (20 capítulos)
│   └── edr/                   ← Engineering Decision Records (último: 0018)
├── src/orion/                 ← código do Notebook (Mission Core + Kernel)
│   ├── kernel/                ← Event Bus, Service Registry, HAL, Watchdog
│   ├── communication/         ← TCP (Raspberry), enquadramento, heartbeat (Cap 14)
│   ├── mission/               ← IA (Ollama), planejador, coordenação (Cap 7)
│   ├── vision/                ← OpenCV, YOLO, reconhecimento facial (Cap 8)
│   ├── voice/                 ← Whisper + Piper + wake word "Fofão" (Cap 9)
│   ├── display/               ← avatar na tela do notebook (Cap 13)
│   └── diagnostics/           ← autotestes, métricas, saúde (Cap 16)
├── motion_core/               ← código do Raspberry Pi — deploy separado
│   ├── navigation/            ← patrulha, seguimento, fusão de sensores (Cap 12)
│   ├── memory/                ← API de memória + Database Manager no SSD (Caps 11, 15)
│   ├── webui/                 ← interface web: dashboard, mapa, diagnóstico (Cap 13)
│   └── bridge/                ← ponte serial com o Arduino (Cap 14)
├── firmware/hardware_core/    ← PlatformIO / Arduino Mega (Cap 10)
├── tests/
│   ├── unit/                  ← pytest; portas seriais e sockets simulados
│   └── integration/           ← cenários INT-01..09 (Cap 19)
└── tools/                     ← scripts de dev (simuladores, gerador de massa)
```

## Comandos

```bash
# Ambiente (Notebook)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Rodar o sistema
python -m orion                      # boot completo (Cap 6, seção 4)
python -m orion --sim                # com Raspberry/Arduino simulados

# Testes
pytest tests/unit -q                 # unitários (rodar após TODA mudança)
pytest tests/integration -q -m sim   # integração com simuladores

# Firmware
cd firmware/hardware_core && pio run          # compilar
cd firmware/hardware_core && pio run -t upload # gravar no Mega
```

## Convenções de código

- Python: type hints obrigatórios, `ruff` para lint, docstrings em português.
- Eventos nomeados em inglês minúsculo com ponto: `motion.obstacle_front`,
  `vision.person_detected` (lista oficial nos Caps 5–16).
- Mensagens do protocolo: JSON compacto conforme Cap 5 seção 5 — o mesmo
  formato nos enlaces TCP (Notebook↔Raspberry) e Serial (Raspberry↔Arduino).
- Commits: `[modulo] descrição` (ex.: `[kernel] event bus com prioridades`).
- Todo bug corrigido ganha teste de regressão (Cap 19).
- Firmware: máquina de estados do Cap 10 seção 4; nunca bloquear o loop
  principal (sem `delay()` em código de produção).

## Ordem de implementação

Siga `PLANO_IMPLEMENTACAO.md`. Resumo: Fase 0 (esqueleto) → Fase 1 (Kernel +
Event Bus) → Fase 2 (Comunicação TCP + Serial + firmware básico) → Fase 3
(Banco/Memória) → Fase 4 (Hardware Core completo) → Fase 5 (Vision no
Notebook) → Fase 6 (Voz + IA) → Fase 7 (Motion Core/Navegação no Raspberry +
autocalibração) → Fase 8 (Avatar + interface web) → Fase 9 (Diagnóstico/Segurança) →
Fase 10 (validação Cap 19).

**Não avance de fase sem os testes da fase atual passando.**

## Contexto do usuário

O mantenedor está aprendendo desenvolvimento de software. Ao trabalhar neste
projeto: explique as decisões em português simples, prefira código claro a
código esperto, comente trechos não óbvios e sempre diga como testar o que
foi feito.
