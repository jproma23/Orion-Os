# Ouvidos do robô (ESP32 + 2 mics I2S)

Um ESP32 WROOM separado do corpo, só com dois microfones INMP441. Ele lê os
dois mics e manda o áudio cru por WiFi para o Voice Core no Notebook. Toda a
conta (VAD, Whisper, direção da voz) fica no Notebook.

> Decidido em 03/08: os ouvidos ficam **separados** do corpo. O áudio não cabe
> no cabo serial do corpo.

## Pinos (medidos, não lembrados)

| INMP441 (os dois mics) | ESP32 |
|---|---|
| SCK / BCLK | **GPIO 26** |
| WS / LRCL  | **GPIO 25** |
| SD / DOUT  | **GPIO 33** |
| VDD | 3,3 V |
| GND | GND |
| L/R | um mic no GND (esquerdo), o outro no 3,3 V (direito) |

Os dois mics dividem SCK, WS e SD; o L/R diferente é o que separa os canais.

## Rede

| | |
|---|---|
| Áudio | TCP **9100**, int16 little-endian, estéreo intercalado (esq/dir), **16000 Hz** (64 KB/s) |
| Nome | `ouvidos.local` (mDNS), hostname `ouvidos` |
| IP | DHCP — **10.20.20.197** em 31/10/2026 |
| WiFi | credenciais **já gravadas na NVS** (rede KAMALL); `WiFi.begin()` vai sem senha no código. Gravar firmware não apaga a NVS. Sem rede em 30 s no boot, a placa **reinicia e tenta de novo** (desde 10/11; antes ficava surda até alguém tirar da tomada). |
| OTA | ArduinoOTA, UDP 3232, **com senha** desde 10/11 (ver "Senha do OTA") |

Só aceita **um cliente TCP por vez**. Se o Voice Core estiver conectado, um
teste seu derruba ele (ele reconecta sozinho em ~3 s).

## Senha do OTA

A senha mora em `include/segredos.h`, que **não vai para o git**
(`.gitignore` desta pasta). Para criar:

```bash
cp include/segredos.exemplo.h include/segredos.h   # e troque a senha
```

O mesmo arquivo serve ao firmware (que exige a senha) e ao upload
(`ota_senha.py` passa `--auth` ao espota). Sem ele, o firmware compila **sem
senha** e avisa no log do boot — não quebra o build.

A primeira gravação com senha passa mesmo assim (a placa de hoje não pede
senha). Depois dela, sem a senha certa o upload falha com
`Authentication Failed`. O `diagnostico/` usa a **mesma** senha (lê o
`include/segredos.h` daqui de cima).

## Gravar

O dono tem **um cabo USB só, e ele fica no corpo**. Por isso:

```bash
cd firmware/ouvidos_esp32
~/.venv-pio/bin/pio run -e ota -t upload      # pelo ar
```

**REGRA: todo firmware deste ESP32 tem que trazer o ArduinoOTA.** Um firmware
sem OTA (como a `varredura/`) obriga a usar o cabo de novo na próxima gravação.

Com cabo (no Notebook aparece como CH340 em `/dev/ttyUSB0`):
`pio run -e esp32dev -t upload --upload-port /dev/ttyUSB0` — o reset automático funciona nesta placa.

## Ganho

`DESLOCAMENTO_GANHO = 12` em `src/main.cpp` (o mic entrega 24 bits dentro de
32; o deslocamento escolhe quantos bits de cima ficam). Medido em 31/10:

| | rms (fração da escala) |
|---|---|
| silêncio / ruído de fundo | ~0,013 |
| voz do dono, perto | ~0,15 |

Com 13 a voz chegava a 0,006 e o Whisper descartava "Jarvis" por confiança
baixa. Cada passo a menos dobra o volume; há um limite (saturação) para um
grito não virar estalo.

## Diagnóstico (quando o robô fica surdo)

1. **O fluxo chega e é tudo zero?** Leia 4 s da porta 9100 e conte amostras ≠ 0.
   Mic vivo **nunca** dá zero absoluto, nem em silêncio.
2. Grave o firmware `diagnostico/` (tem OTA; resultado em texto na TCP **9101**:
   `nc ouvidos.local 9101`). Ele alterna o resistor interno do pino SD:

   | O que aparece | Significa |
   |---|---|
   | `FFFFFFFF` com PULL-UP e `0` com PULL-DOWN | **ninguém dirige o SD**: mic sem 3,3 V/GND, sem clock (SCK/WS soltos) ou fio SD solto |
   | valores variando, parecidos nos 3 modos | mic vivo |

3. `varredura/` testa ~6000 combinações de SCK/WS/SD para achar pinos
   desconhecidos. **Não tem WiFi nem OTA** — só com cabo, e depois tem que
   regravar o normal pelo cabo também.

### Histórico de defeitos

- **25/10/2026** — os dois mics em zero absoluto. A varredura não achou sinal
  em nenhuma combinação.
- **31/10/2026** — o diagnóstico mostrou o SD seguindo o resistor (ninguém
  dirigindo). Causa: **o fio WS tinha soltado do GPIO 25**. Soldado, os dois
  canais voltaram (ruído ~170/32768, um bipe de teste a 3.600–8.500 rms).

## Arquivos

| Pasta | O que é |
|---|---|
| `src/` | firmware normal (áudio TCP 9100 + OTA) |
| `diagnostico/` | lê o SD com pull-up/pull-down/sem resistor, WiFi + OTA, texto na TCP 9101 |
| `varredura/` | busca de pinos I2S, só serial, **sem OTA** |
