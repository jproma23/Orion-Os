<p align="center">
  <img src="docs/assets/capa.webp" alt="ORION OS — o robô Sentinela na bancada" width="100%">
</p>

<h1 align="center">ORION OS</h1>

<p align="center">
  <strong>Plataforma de robótica modular, offline-first.</strong><br>
  Primeiro robô: <strong>Fofão</strong>.
</p>

<p align="center">
  <a href="https://jproma23.github.io/Orion-Os/"><img alt="Diário de bordo" src="https://img.shields.io/badge/diário_de_bordo-2a3f8f?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="C++" src="https://img.shields.io/badge/C++-ESP32-00979D?style=flat-square&logo=espressif&logoColor=white">
  <img alt="Testes" src="https://img.shields.io/badge/testes-305_passando-2e7d32?style=flat-square">
</p>

---

Um robô que percebe o ambiente, decide sozinho o que fazer e conversa com
quem mora na casa. **Tudo que mantém o robô seguro roda local**: visão,
escuta, navegação, memória e segurança. A IA da conversa é remota, por API
(Gemini, EDR-0021). Sem internet, o robô continua seguro, mas conversa
pouco.

**[→ Diário de bordo do desenvolvimento](https://jproma23.github.io/Orion-Os/)**
— cada sessão registrada no dia em que aconteceu, os acertos e os bugs que
custaram caro.

## Arquitetura

Duas máquinas e três ESP32. A divisão vigente é a da
[EDR-0036](docs/edr/EDR-0036-notebook-volta.md) (30/09/2026): o Notebook faz o
trabalho pesado que pode esperar, e o Pi faz o que não pode falhar e guarda o
estado.

```
   ESP32 ouvidos ──Wi-Fi──► Notebook 8 GB         Vision + Voice   YOLO, rostos, Whisper, IA, Piper
   (2 mics I2S)                  │  TCP (só eventos, nunca vídeo/áudio)
                             Raspberry Pi 4 + SSD  Mission + Motion memória, decisão, navegação, LiDAR, web UI
                              │ serial      │ USB
                     ESP32 do corpo     ESP32-S3 (tela)
                     motores, servos,   rosto e toque
                     bússola, segurança
```

| Unidade | Hardware | Linguagem | Responsabilidade |
|---|---|---|---|
| **Vision + Voice Core** | Notebook 8 GB (Debian), webcam e alto-falante USB | Python 3.13 | Rostos, YOLO, palavra de ativação, Whisper, chamadas da IA, Piper. Não guarda estado |
| **Mission Core** | Raspberry Pi 4 (4 GB) + SSD 500 GB | Python 3.13 | Memória, recados, GOAP, decisões |
| **Motion Core** | Raspberry Pi 4 | Python 3.13 | Kernel, Behavior Core, LiDAR, fusão de sensores, interface web |
| **Hardware Core** | ESP32 (DevKit) | C++ (PlatformIO) | Tempo real: motores DC com encoder, servos, bússola, parada de emergência |
| **Rosto** | ESP32-S3, tela 3,5" | C++ (ESP-IDF) | Olhos, boca sincronizada com a fala, toque |

> **Em 25/10/2026 a EDR-0036 está executada pela metade:** o Mission Core
> ainda roda no Notebook, com código de agosto. Ver a EDR.

As regras invioláveis estão em [`ARQUITETURA.txt`](ARQUITETURA.txt).

## O que já funciona

- **Kernel próprio** — event bus assíncrono com fila de prioridades, service
  registry, health monitor, watchdog com escalonamento e boot manager
- **Behavior Core** — arbitragem de prioridade entre comportamentos
  autônomos: repouso, atender, vigília e desvio de obstáculo
- **Protocolo** entre Pi e corpo (ESP32): quadros JSON com CRC, ACK,
  heartbeat e reconexão automática
- **Visão** — reconhecimento facial: o robô sabe quem é da casa e dispara
  alerta para rosto desconhecido
- **Voz** — wake word, VAD, Whisper e resposta falada pelo Piper; a IA da conversa é o Gemini, por API
- **Memória** — diário de observações em banco, com consulta determinística
  ("você viu o fulano hoje?") separada da conversa livre
- **Navegação** — LiDAR STL-50B2, fusão de odometria e bússola publicando `motion.position`
- **Interface web** — dashboard com o mapa do LiDAR, telemetria ao vivo,
  diagnóstico e configuração

## Estrutura

```
src/orion/          kernel, communication, mission (Pi), vision e voice (Notebook), display
motion_core/        Motion Core: behavior, navigation, memory, webui
firmware/           Hardware Core: firmware do ESP32 do corpo (PlatformIO)
docs/ses/           Especificação do sistema, capítulos 01 a 20
docs/edr/           Registros de decisão de arquitetura
docs/journal.md     Diário de bordo — vira o blog via tools/build_blog.py
config/orion.yaml   Configuração única do sistema
tests/              305 testes
```

## Como rodar

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m orion --sim     # sobe o sistema em simulação, sem hardware
python -m orion           # boot real
```

Testes e lint:

```bash
tools/check.sh            # ruff + pytest
```

O firmware do corpo (ESP32) fica em `firmware/hardware_core/`, compilado com
PlatformIO. Os pinos estão em `firmware/hardware_core/include/pins.h`.

## Documentação

| | |
|---|---|
| [Diário de bordo](https://jproma23.github.io/Orion-Os/) | Como o projeto foi construído, dia a dia |
| [`ARQUITETURA.txt`](ARQUITETURA.txt) | Regras invioláveis — leia antes de implementar |
| [`docs/ses/`](docs/ses/) | Especificação completa, capítulos 01–20 |
| [`docs/edr/`](docs/edr/) | Decisões de arquitetura e o porquê de cada uma |
| [`PLANO_IMPLEMENTACAO.md`](PLANO_IMPLEMENTACAO.md) | Fases de desenvolvimento |

---

<p align="center">
  Desenvolvido por <strong>João Paulo</strong> ·
  <a href="https://github.com/jproma23">@jproma23</a>
</p>
