# Hardware Core (ESP32 DevKit)

Firmware PlatformIO (Cap 10) do corpo: 2 motores DC JGB37-520 com encoder
(L298N, PID de velocidade + malha da reta), servos pan/tilt, bússola
(HMC5883L/QMC5883L), DHT, lanterna e o homem-morto.
Comunicação USB Serial (250000 baud) exclusivamente com o Raspberry Pi
(Motion Core). Mapa de pinos e o porquê de cada um: `include/pins.h`.

Reescrito em 2026-11-10 (auditoria C4-05): este README ainda descrevia
TB6600/NEMA17, ultrassons e "encoders cancelados" — tudo isso saiu.

- **Encoders:** existem e são a única medida de velocidade do PID (canal A
  de cada roda; o B está ligado e ainda não é lido). Esquerdo 14/27,
  direito 34/35.
- **Distância:** o firmware não mede. O LiDAR STL-50B2 fica no Raspberry.
- **Sem relés de energia:** o L298N vai direto na fonte e o ventilador direto
  na energia. FAN_ON/FAN_OFF recebem NACK. A lanterna é o único relé (GPIO 17).
- **Tranco no boot:** do reset até `motores.iniciar()` (primeira linha do
  `setup()`) os IN do L298N flutuam com a ponte energizada. Software não
  fecha essa janela: **pull-down de 10k de cada IN1..IN4 para o GND** (hardware).
- **Homem-morto:** 5 s sem comando de MOVIMENTO para as rodas (SET_PAN_TILT
  e afins não renovam). Movimento com fim (MOVE_DISTANCE, TURN com graus)
  só continua com o HEARTBEAT do Pi chegando (2,5 s). Ver `safety_manager.h`.
- **Comando desconhecido:** NACK `{"motivo": "comando_desconhecido"}` no
  lugar do ACK.
- **Versão:** `versao_git.py` grava o commit no `.bin`; a placa responde em
  WHO_ARE_YOU (`versao_modulo`, `firmware_git`) e na telemetria completa.
- MPU6050 (IMU) descartada (2026-08-18): a bússola é o único dispositivo I2C.
