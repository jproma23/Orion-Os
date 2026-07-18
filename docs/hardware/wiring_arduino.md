# Fofão — Guia de ligação elétrica do Arduino Mega (Hardware Core)

Fonte canônica dos pinos: `firmware/hardware_core/include/pins.h`. Este
documento é a versão legível para montagem de bancada — se os dois
divergirem no futuro, `pins.h` é que vale.

**Status em 2026-07-17: nenhum periférico está fisicamente montado ainda.**
Este guia é para quando a montagem começar. O firmware (Fase 4) já está
pronto e testado no nível de protocolo com o Mega real (sem periféricos) —
falta só a montagem física para o teste de bancada completo.

---

## 1. Domínios de energia — ler antes de ligar qualquer coisa

Os motores **não** são alimentados pelo Mega nem pela Raspberry Pi.

| Domínio | Fonte | Tensão | Alimenta |
|---|---|---|---|
| **Lógica** | USB da Raspberry Pi | 5V | Mega, HC-SR04, MPU6050, DHT, lado de controle dos TB6600 |
| **Motor** | Fonte externa dedicada | 12–24V (conforme o NEMA17) | 2× TB6600 → NEMA17 |

O GND da fonte de motor precisa estar em comum com o GND do Mega — sem
isso, STEP/DIR não têm referência e os motores não respondem (ou respondem
errado). Misturar as duas fontes queima o driver ou a placa.

## 2. Raspberry ↔ Arduino

Um único cabo USB comum (aparece como `/dev/ttyUSB0`, adaptador CH340
nesta montagem) — sem fiação GPIO entre as duas placas. Já testado: o
protocolo ORION responde WHO_ARE_YOU, ACK e HEARTBEAT por esse cabo.

## 3. Motores — 2× NEMA17 via 2× TB6600 (pinos confirmados)

| Pino Mega | Sinal | Destino no driver |
|---|---|---|
| 2 | STEP esquerdo | PUL+ (driver esquerdo) |
| 3 | DIR esquerdo | DIR+ (driver esquerdo) |
| 4 | STEP direito | PUL+ (driver direito) |
| 5 | DIR direito | DIR+ (driver direito) |
| 6 | ENABLE (compartilhado) | EN+ dos dois drivers, em paralelo |
| GND | Referência comum | PUL-, DIR-, EN- dos dois drivers |

Se o TB6600 não tiver ENABLE, deixe EN+ solto — o firmware já assume
"sempre habilitado" nesse caso (não precisa mudar nada).

TB6600 costuma ter EN+ ativo em nível baixo — o firmware já assume isso
(`ENABLE_ATIVO_EM_BAIXO = true` em `motor_manager.h`). Se os motores só
habilitarem com o pino em HIGH, inverta essa constante.

Sentido de rotação depende de qual par de fios da bobina vai em qual
terminal do TB6600 — se um motor girar ao contrário, inverta o par A+/A-
*ou* ajuste o sinal de DIR no firmware, sem precisar reabrir a fiação.

## 4. Sensores confirmados

### HC-SR04 — ultrassom frontal (fixo, sem servo nesta fase)

| Pino Mega | Sinal |
|---|---|
| 22 | TRIG |
| 23 | ECHO |
| 5V | VCC |
| GND | GND |

### MPU6050 / MPU9250 — IMU, barramento I2C

| Pino Mega | Sinal |
|---|---|
| 20 | SDA (fixo do Mega) |
| 21 | SCL (fixo do Mega) |
| 5V ou 3.3V | VCC (confira o módulo) |
| GND | GND |

### DHT11 / DHT22 — temperatura e umidade

| Pino Mega | Sinal |
|---|---|
| 24 | DATA |
| 5V | VCC |
| GND | GND |

Firmware assume **DHT11** por padrão (`dht_manager.h`) — se o sensor físico
for DHT22, troque o segundo argumento do construtor `DHT` nesse arquivo
(uma linha).

## 5. Servos Pan/Tilt (Cap 8 s.8; Cap 10 s.2) — pronto para montar

Servo hobby padrão de 3 fios (SG90 ou similar) — cores variam por
fabricante, mas o padrão mais comum é:

| Fio do servo (cor comum) | Sinal | Pino Mega/fonte |
|---|---|---|
| Laranja ou amarelo | PWM (sinal) | **Pan → pino 10** / **Tilt → pino 11** |
| Vermelho | VCC (+5V) | fonte de 5V dedicada aos servos (ver aviso abaixo) |
| Marrom ou preto | GND | comum com o GND do Mega |

**Domínio de energia dos servos — mesmo cuidado dos motores:** servos
puxam picos de corrente (500 mA–1 A cada, principalmente ao encontrar
resistência) que o 5V lógico do Mega (vindo do USB da Raspberry) não
aguenta com folga — pode causar reset do Mega no meio de um movimento.
Recomendado: uma fonte 5V dedicada só para os servos (pan, tilt e,
futuramente, o do radar), com GND em comum com o Mega. Não ligue o VCC dos
servos direto no pino 5V do Mega.

Comando novo no firmware para testar: `SET_PAN_TILT` com payload
`{"pan_graus": <-80..80>, "tilt_graus": <-30..45>}` (ângulos relativos ao
centro — o firmware converte para 0–180° internamente, com centro em 90°).
Já testado no Mega real via protocolo (ACK ok) — só falta o servo físico
responder.

## 6. Periféricos reservados (ainda sem fiação — pinos já reservados no firmware)

Estes pinos **não têm nada físico ligado ainda**. O firmware já reserva o
pino e degrada bem na ausência (leitura zerada/sem eco, nunca trava) — só
ligar o componente quando a peça chegar, sem precisar mudar código.

| Periférico | Pino Mega | Observação |
|---|---|---|
| Encoder esquerdo | 18 | interrupt externo do Mega |
| Encoder direito | 19 | interrupt externo do Mega |
| Ultrassom traseiro — TRIG | 26 | |
| Ultrassom traseiro — ECHO | 27 | |
| Servo do radar (varredura 0–180°) | 9 | PWM — mesma fonte dedicada dos servos pan/tilt |
| LED lanterna | 25 | |

Pan/tilt (pinos 10/11) já tem esquema completo na seção 5 acima.

## 7. Ordem de montagem recomendada

1. **Tudo desligado** — Mega desconectado da Pi, fontes de motor e de servos desligadas da tomada.
2. **Ligue os GNDs primeiro** — Mega GND ↔ GND dos dois TB6600 ↔ GND da fonte de motor ↔ GND da fonte dos servos.
3. **Ligue os sinais de controle dos motores** — STEP/DIR/ENABLE (tabela da seção 3).
4. **Ligue os sensores** — HC-SR04, IMU (I2C), DHT — confira 5V vs. 3.3V em cada módulo.
5. **Ligue os servos pan/tilt (e o do radar, se já tiver)** — sinal nos pinos 10/11/9, VCC na fonte dedicada (seção 5) — fonte ainda desligada.
6. **Ligue os motores nos terminais de saída do TB6600** — bobinas do NEMA17 (A+/A-/B+/B-) — fonte de motor ainda desligada.
7. **Confira os dip switches de corrente do TB6600** — conforme a corrente nominal do NEMA17 (etiqueta do motor).
8. **Energize só o lado lógico** — conecte o USB Mega↔Pi. `python tools/sim_arduino.py` ou o teste real deve mostrar HEARTBEAT chegando e WHO_ARE_YOU respondendo. Teste `SET_PAN_TILT` aqui também (servos já devem se mexer, mesmo com a fonte deles ainda desligada, se estiverem sacando do proprio 5V logico - senao, so vao se mexer no proximo passo).
9. **Por último, ligue as fontes de motor e de servos** — observe cheiro/fumaça nos primeiros segundos. Teste um movimento pequeno (MOVE_DISTANCE curto) antes de qualquer missão longa, com as rodas suspensas.

## 8. Checklist antes de energizar

- [ ] GND do Mega, dos dois TB6600, da fonte de motor e da fonte dos servos estão todos em comum
- [ ] Fontes de motor e de servos estão desligadas da tomada até o passo 9
- [ ] Nenhum fio de motor encostando em outro (curto nas bobinas)
- [ ] Dip switches de corrente do TB6600 conferidos com a etiqueta do NEMA17
- [ ] VCC dos sensores no nível certo (5V ou 3.3V, conforme o módulo)
- [ ] Cabo USB Mega↔Pi firme, sem forçar o conector
- [ ] Rodas suspensas (sem tocar o chão) para o primeiro teste de movimento
