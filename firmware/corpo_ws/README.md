# Corpo — firmware do ESP32 que fala WebSocket

O "corpo" do Fofão: motores, encoders, pan/tilt, bússola, DHT e lanterna,
controlados de um ESP32 que conversa com o Raspberry por **WiFi/WebSocket**.

## Por que este diretório existe

Até 2026-09-20 o ESP32 do corpo rodava um firmware WebSocket **sem fonte** —
não estava no repositório, nem no Pi, nem no Windows. A única cópia era a
flash do próprio chip, e o protocolo teve de ser recuperado com `strings` no
binário (backup em `~/backups/firmware_corpo/`, fora do repo porque contém a
senha do WiFi em texto claro).

Este é aquele firmware reescrito, com fonte, e com quatro buracos tapados.

## O que mudou em relação ao firmware sem fonte

| | antigo | aqui |
|---|---|---|
| Andar uma distância | não sabia — quem contava pulsos e freava era o **Pi**, com o WiFi dentro da malha | `move`/`turn` terminam **no ESP32**, com PID e encoder locais |
| Freio próprio | nenhum: perder o WiFi andando = continuar andando | watchdog para os motores após 1,5 s sem comando |
| Confirmação | sem ACK — a ponte no Pi precisava forjar | `{"ack":<id>,"ok":true}` de verdade |
| Bússola e DHT | ausentes da telemetria | de volta |

Os comandos antigos (`drive`, `stop`, `gimbal`, `ping`) **continuam valendo**
e a telemetria mantém os nomes antigos, então a ponte do Pi
(`motion_core/navigation/ponte_corpo_ws.py`) funciona com os dois firmwares —
ela detecta qual está do outro lado pela telemetria, não por configuração.

## Protocolo

Tudo JSON, pela porta 81. `id` é opcional e volta no `ack`.

**Comandos**

    {"cmd":"ping"}                            -> {"pong":true}
    {"cmd":"stop"}
    {"cmd":"drive","left":-255..255,"right":-255..255}
    {"cmd":"move","cm":50,"vel":60}            distância finita
    {"cmd":"turn","graus":90,"vel":40}         + horário, - anti-horário
    {"cmd":"gimbal","pan":-30,"tilt":10}       EM GRAUS, -90..90
    {"cmd":"lanterna","on":true}

**Telemetria** (~2 Hz, broadcast)

    {"tof":[-1,-1,-1,-1],"obstacle_distance":-1,
     "passos_esq":0,"passos_dir":0,"rssi":-61,
     "pan_us":1500,"tilt_us":1500,
     "estado":"IDLE","em_movimento":false,"safe_mode":false,
     "bussola_calibrada":true,"rumo_graus":137.5,
     "temperatura_c":24.3,"umidade_percent":61.0}

`rumo_graus`, `temperatura_c` e `umidade_percent` **só aparecem quando a
leitura vale**. Campo ausente é "não sei"; zero seria uma medida falsa.

## Compilar e gravar

    cp include/segredos_wifi.h.exemplo include/segredos_wifi.h
    # preencha SSID e senha — este arquivo está no .gitignore
    ~/.venv-pio/bin/pio run                       # compila
    ~/.venv-pio/bin/pio run -t upload             # grava

**Antes de gravar**: pare o Motion Core (`systemctl --user stop
orion-motion`) — ele segura a serial. E lembre que gravar **apaga o firmware
antigo**, que não tem fonte; a única volta é o backup da flash.

## Notas de build que custaram tempo

- **`lib_ldf_mode` fica no padrão.** `chain+` e `deep+` fazem o LDF reavaliar
  as dependências e perder as bibliotecas do *framework*: a WebSockets deixa
  de achar `WiFi.h`. Medido — ela compila sozinha no modo padrão e quebra nos
  dois modos "melhores".
- Por isso `EEPROM`, `Wire`, `WiFi` e `WiFiClientSecure` estão declaradas à
  mão em `lib_deps`, mesmo vindo do framework.
- Os managers (`motor_manager.h`, `bussola_manager.h`, ...) são **incluídos**
  do `hardware_core` por `-I`, não copiados: duas cópias divergiriam na
  primeira correção feita só de um lado.

## O que ainda não foi medido

- **A escala de `drive`** (-255..255) é a herdada do firmware antigo; com os
  motores montados, conferir com as rodas no ar.
- **A redução da caixa** (1:90) e a **bitola** (0,45 m) continuam sendo
  suposição, e entram em `andarDistancia`/`girarGraus` via
  `motor_manager.h`. Medir antes de confiar em centímetro ou grau.
