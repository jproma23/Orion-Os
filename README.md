# ORION OS

Plataforma de robótica modular e **offline-first** (Internet apenas habilita extras). Primeiro robô: **Fofão**.

- **Especificação oficial:** `docs/ses/` (Capítulos 01–20)
- **Guia para o Claude Code:** `CLAUDE.md`
- **Plano de desenvolvimento:** `PLANO_IMPLEMENTACAO.md`
- **Configuração:** `config/orion.yaml`
- **Arquitetura vigente:** `docs/edr/EDR-0018-arquitetura-tcc.md` + `docs/edr/EDR-0019-divisao-final-e-ssd.md`

## Arquitetura (em cadeia — conforme TCC)

    Notebook 8GB (Mission Core: IA + Visão + Voz + Avatar)
            │  Ethernet (TCP)
    Raspberry Pi 4 + SSD 500GB (Motion Core: navegação + banco + web UI)
            │  USB Serial
    Arduino Mega (Hardware Core: motores/servos/sensores)

Comece pelo `CLAUDE.md`.
