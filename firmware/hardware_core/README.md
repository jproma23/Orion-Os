# Hardware Core (ESP32 DevKit)

Firmware PlatformIO (Cap 10): motores (TB6600/NEMA17, passos por software),
servos, ultrassons, bússola, lanterna e segurança reativa.
Comunicação USB Serial exclusivamente com o Raspberry Pi (Motion Core). Envia
o pacote Radar Inteligente periodicamente (Cap 5, seção 7).

Encoders físicos CANCELADOS (2026-08-17): a odometria é por passos
comandados; a navegação usa o LiDAR LD06 no Raspberry.
MPU6050 (IMU) DESCARTADA (2026-08-18): sem inclinação nem impacto no
firmware; a bússola é o único dispositivo I2C.
