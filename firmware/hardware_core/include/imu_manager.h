// IMU Manager (Cap 10 secao 7) - MPU6050 via I2C (pinos 20/21, CONFIRMADO).
//
// Usada para: correcao fina de execucao local, deteccao de inclinacao e de
// impacto (Cap 10 s.7). A navegacao de verdade fica no Motion Core
// (Raspberry) - aqui e so leitura e um limiar de seguranca reativa.
#pragma once

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>

namespace orion {

constexpr float LIMITE_INCLINACAO_GRAUS = 20.0f;  // igual ao default motion.tilt_limit_degrees
constexpr float LIMITE_IMPACTO_G = 2.5f;

class ImuManager {
 public:
  bool iniciar() {
    _conectado = _mpu.begin();
    if (_conectado) {
      _mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
      _mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    }
    return _conectado;
  }

  void atualizar() {
    if (!_conectado) return;
    sensors_event_t accel, gyro, temp;
    _mpu.getEvent(&accel, &gyro, &temp);

    float ax = accel.acceleration.x;
    float ay = accel.acceleration.y;
    float az = accel.acceleration.z;

    _inclinacaoGraus = degrees(atan2(sqrt(ax * ax + ay * ay), az));
    float magnitudeG = sqrt(ax * ax + ay * ay + az * az) / 9.80665f;
    _impactoDetectado = magnitudeG > LIMITE_IMPACTO_G;
  }

  bool conectado() const { return _conectado; }
  float inclinacaoGraus() const { return _inclinacaoGraus; }
  bool inclinacaoCritica() const { return _inclinacaoGraus > LIMITE_INCLINACAO_GRAUS; }
  bool impactoDetectado() const { return _impactoDetectado; }

 private:
  Adafruit_MPU6050 _mpu;
  bool _conectado = false;
  float _inclinacaoGraus = 0;
  bool _impactoDetectado = false;
};

}  // namespace orion
