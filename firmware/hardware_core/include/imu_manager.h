// IMU Manager (Cap 10 secao 7) - MPU6050 via I2C (pinos 20/21, CONFIRMADO).
//
// Usada para: correcao fina de execucao local, deteccao de inclinacao e de
// impacto (Cap 10 s.7). A navegacao de verdade fica no Motion Core
// (Raspberry) - aqui e so leitura e um limiar de seguranca reativa.
#pragma once

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <EEPROM.h>

namespace orion {

constexpr float LIMITE_INCLINACAO_GRAUS = 20.0f;  // igual ao default motion.tilt_limit_degrees
// Default do limiar de impacto. ESTIMATIVA, nunca medida - por isso e
// ajustavel em runtime (definirLimiteImpacto) e gravado na EEPROM.
// Referencia medida em 2026-07-19: robo parado le 1,01-1,02 G (gravidade),
// com ruido de +-0,01 G. Falta medir o piso com o robo ANDANDO (vibracao
// levanta esse piso) e o pico de uma batida real.
constexpr float LIMITE_IMPACTO_G = 2.5f;
// Quanto tempo o flag de impacto fica ligado depois do tranco. Precisa ser
// maior que o intervalo da TELEMETRY (500ms) para nao existir tranco que
// passe despercebido entre dois quadros.
constexpr unsigned long JANELA_IMPACTO_MS = 1000;

// ---- Rumo relativo (yaw) por integracao do giroscopio (2026-08-13) ----
//
// Por que existe: com UM encoder so, a distancia vem do encoder e o RUMO
// tem que vir de outro lugar (fusao_sensores.py). O acelerometro nao serve
// - ele mede a gravidade, e girar no plano nao muda nada nela. Quem sabe
// girar e o giroscopio, que a MPU6050 ja entrega na mesma leitura I2C que
// esta classe ja faz a cada 50 ms. Custo adicional: zero leitura extra.
//
// O QUE ISTO NAO E: nao e bussola. Isto e rumo RELATIVO - conta quanto
// girou desde que ligou (ou desde zerarYaw()), nao para onde o Norte fica.
// E deriva: erro de vies nao corrigido vira angulo somado para sempre. Quem
// da o rumo absoluto e a BussolaManager, e ela so vale com o robo parado
// (motor de passo perto distorce o campo mais que a propria Terra,
// EDR-0024). Os dois juntos e que fecham a conta: giroscopio enquanto anda,
// bussola para rezerar quando para.

// Abaixo desta taxa a leitura e tratada como robo PARADO: nao integra (o
// ruido viraria giro fantasma) e aproveita para refinar o vies.
//
// 0,02 rad/s = 1,15 graus/s. O preco honesto dessa escolha: uma rotacao
// mais lenta que isso e invisivel - girando a 1 grau/s por 10 s, o robo
// perde 10 graus e nao sabe. Aceito porque este chassi gira na casa de
// dezenas de graus por segundo; se um dia existir manobra fina de verdade,
// e este numero que precisa cair (e ai o ruido do giro passa a entrar).
constexpr float LIMIAR_REPOUSO_RAD_S = 0.02f;

// Quanto de cada leitura em repouso entra no vies. Baixo de proposito: o
// vies tem que seguir a deriva termica (que leva minutos), nao o ruido de
// cada amostra.
constexpr float FATOR_APRENDIZADO_VIES = 0.01f;

// Antes disso o vies ainda nao vale nada e o yaw seria lixo somado - por
// isso `yawValido()` responde false e a telemetria nem publica o campo. A
// 50 ms por leitura, sao ~2,5 s de robo parado depois de ligar.
constexpr uint16_t AMOSTRAS_VIES_MINIMAS = 50;

// Calibracao do vetor "para baixo" gravada na EEPROM.
//
// Por que existe: o modulo nao esta colado perfeitamente alinhado com o
// chassi (medido em 2026-07-19: 9.3 graus com o robo nivelado). Nao da para
// simplesmente subtrair esses 9.3 graus, porque a inclinacao e um ESCALAR
// (angulo ate o eixo Z, sempre positivo, sem direcao) - subtrair um valor
// fixo erraria conforme o lado para onde o robo inclina.
//
// A correcao certa e guardar o vetor da gravidade lido com o robo nivelado
// e medir o angulo ENTRE o vetor atual e esse de referencia. Assim o zero
// fica no lugar certo e o angulo continua correto em qualquer direcao.
constexpr int EEPROM_ENDERECO_IMU = 0;
constexpr uint16_t EEPROM_MAGICO_IMU = 0x0110;  // marca "ja calibrado"

// Limiar de impacto em endereco SEPARADO da calibracao de proposito:
// regravar um nao pode apagar o outro (a calibracao do vetor ja foi feita
// com o robo na bancada e nao deve se perder ao ajustar o limiar).
constexpr int EEPROM_ENDERECO_LIMIAR = 32;
constexpr uint16_t EEPROM_MAGICO_LIMIAR = 0x0111;

struct LimiarImpactoGravado {
  uint16_t magico;
  float limiteG;
};

struct CalibracaoImu {
  uint16_t magico;
  float refX;
  float refY;
  float refZ;
};

class ImuManager {
 public:
  bool iniciar() {
    _conectado = _mpu.begin();
    if (_conectado) {
      // ±8 G, nao ±4 G (mudado em 2026-07-19). Com teto de 4 G e limiar de
      // impacto em 2,5 G sobrava quase nada de faixa util: uma batida de
      // verdade SATURAVA o sensor, e o pico medido saia menor do que foi -
      // um esbarrao e uma pancada forte viravam ambos "4 G", impossivel
      // escolher limiar por medida.
      //
      // O custo (metade da resolucao) e irrelevante aqui: o ADC e de 16
      // bits, e o ruido medido com o robo parado foi de +-0,01 G em 93
      // amostras. A inclinacao tambem nao sofre - ela usa a DIRECAO do
      // vetor (normalizado), nao a magnitude.
      _mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
      _mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    }
    carregarCalibracao();
    carregarLimiarImpacto();
    return _conectado;
  }

  void atualizar() {
    if (!_conectado) return;
    sensors_event_t accel, gyro, temp;
    // getEvent() pode falhar sem travar (ex.: NACK pontual no I2C) -
    // ignorar o retorno usaria accel/gyro/temp nao inicializados pra
    // calcular inclinacao/impacto. Se falhar, mantem a ultima leitura
    // valida em vez de calcular sobre lixo (achado real da vistoria de
    // 2026-07-24; o travamento total do barramento e coberto a parte
    // pelo Wire.setWireTimeout() em main.cpp).
    if (!_mpu.getEvent(&accel, &gyro, &temp)) return;

    _ax = accel.acceleration.x;
    _ay = accel.acceleration.y;
    _az = accel.acceleration.z;

    float norma = sqrt(_ax * _ax + _ay * _ay + _az * _az);
    if (norma < 0.001f) return;  // leitura invalida - mantem o valor anterior

    // Angulo entre o vetor lido agora e o vetor de referencia (produto
    // escalar dos dois normalizados). Sem calibracao a referencia e o
    // proprio eixo Z, o que reproduz o comportamento antigo.
    float cosseno = (_ax * _refX + _ay * _refY + _az * _refZ) / norma;
    cosseno = constrain(cosseno, -1.0f, 1.0f);  // protege o acos de erro numerico
    _inclinacaoGraus = degrees(acos(cosseno));

    // Impacto fica ligado por uma JANELA de tempo, nao so no instante em
    // que acontece. Um tranco dura ~10-20ms, mas o IMU so e lido a cada
    // 50ms e a TELEMETRY so sai a cada 500ms - com flag instantaneo (como
    // era ate 2026-07-19) ele quase sempre caia entre duas amostras e
    // ninguem via.
    //
    // A janela apaga sozinha em vez de "zerar na leitura" de proposito: o
    // SafetyManager le o flag todo loop e a telemetria so a cada 500ms, e
    // se a leitura consumisse o evento quem lesse primeiro roubaria do
    // outro - a telemetria voltaria a nunca enxergar nada.
    _aceleracaoG = norma / 9.80665f;
    // Pico entre duas leituras de quem consome: um tranco dura ~10-20ms e o
    // IMU e lido a cada 50ms, entao o valor INSTANTANEO quase nunca pega o
    // topo. Sem guardar o pico nao da para escolher um limiar com base em
    // medida - so no chute, que foi como os 2,5 G originais entraram.
    if (_aceleracaoG > _picoG) {
      _picoG = _aceleracaoG;
    }

    if (_aceleracaoG > _limiteImpactoG) {
      _instanteImpacto = millis();
    }

    // dt REAL, nao INTERVALO_IMU_MS assumido: quem chama isto e o loop de
    // main.cpp, que atrasa quando a serial bloqueia ou o ultrassom espera
    // eco. Integrar com o intervalo nominal enquanto o real foi outro erra
    // o angulo na exata proporcao do atraso - e o loop deste firmware ja
    // e conhecido por esticar (por isso existe o `loop_max_us`).
    unsigned long agoraUs = micros();
    float dt_s = (_ultimaLeituraUs == 0) ? 0.0f : (agoraUs - _ultimaLeituraUs) / 1000000.0f;
    _ultimaLeituraUs = agoraUs;
    _integrarYaw(gyro.gyro.z, dt_s);
  }

  // Le o pico acumulado e ZERA - assim cada quadro de telemetria reporta o
  // maior valor visto desde o quadro anterior, sem perder trancos que
  // aconteceram entre eles.
  float consumirPicoG() {
    float pico = _picoG;
    _picoG = _aceleracaoG;  // recomeca do valor atual, nao de zero
    return pico;
  }

  float aceleracaoG() const { return _aceleracaoG; }
  float limiteImpactoG() const { return _limiteImpactoG; }

  // Ajusta o limiar de impacto e grava na EEPROM. Existe para o limiar ser
  // escolhido a partir de MEDIDA (com o robo andando, batendo de verdade)
  // sem precisar regravar firmware a cada tentativa. O default de 2,5 G
  // nunca foi medido - entrou como estimativa.
  bool definirLimiteImpacto(float limiteG) {
    if (!(limiteG > 1.05f) || limiteG > 7.5f) {
      return false;  // <=1,05 dispararia com a gravidade parada; >7,5 satura
    }
    _limiteImpactoG = limiteG;
    LimiarImpactoGravado dados{EEPROM_MAGICO_LIMIAR, limiteG};
    EEPROM.put(EEPROM_ENDERECO_LIMIAR, dados);
    return true;
  }

  // Congela a orientacao atual como "nivelado" e grava na EEPROM.
  // Chame com o robo parado sobre uma superficie plana.
  bool calibrar() {
    if (!_conectado) return false;
    float norma = sqrt(_ax * _ax + _ay * _ay + _az * _az);
    if (norma < 0.001f) return false;

    _refX = _ax / norma;
    _refY = _ay / norma;
    _refZ = _az / norma;
    _calibrado = true;

    CalibracaoImu dados{EEPROM_MAGICO_IMU, _refX, _refY, _refZ};
    EEPROM.put(EEPROM_ENDERECO_IMU, dados);
    return true;
  }

  // Rumo relativo acumulado, 0..360 graus. So faz sentido depois de
  // yawValido() - antes disso o vies do giroscopio ainda nao foi estimado
  // e o valor e ruido integrado.
  float yawGraus() const { return _yawGraus; }
  bool yawValido() const { return _conectado && _amostrasVies >= AMOSTRAS_VIES_MINIMAS; }

  // Faz do rumo atual o zero. Serve para o Motion Core alinhar o rumo
  // relativo com uma referencia que ele conheca (a bussola com o robo
  // parado, ou o inicio de uma missao).
  void zerarYaw() { _yawGraus = 0.0f; }

  bool conectado() const { return _conectado; }
  bool calibrado() const { return _calibrado; }
  float inclinacaoGraus() const { return _inclinacaoGraus; }
  // Vetor cru da aceleracao (m/s^2). Existe para a BussolaManager compensar
  // a inclinacao SEM fazer uma segunda leitura I2C da MPU6050 - o dado ja
  // foi lido aqui a cada 50 ms, ler de novo so encheria o barramento e a
  // volta do loop (ver loop_max_us em main.cpp).
  float acelX() const { return _ax; }
  float acelY() const { return _ay; }
  float acelZ() const { return _az; }
  bool inclinacaoCritica() const { return _inclinacaoGraus > LIMITE_INCLINACAO_GRAUS; }
  // Verdadeiro durante JANELA_IMPACTO_MS apos o ultimo tranco detectado.
  bool impactoDetectado() const {
    if (_instanteImpacto == 0) return false;
    return (millis() - _instanteImpacto) < JANELA_IMPACTO_MS;
  }

 private:
  // Integra a taxa do giroscopio em angulo, descontando o vies.
  //
  // O vies e o motivo de isto nao ser so uma soma: um MPU6050 parado nao
  // le zero, le algo como 0,01-0,03 rad/s de offset, que muda com a
  // temperatura. Integrando sem descontar, o robo "gira" sozinho parado -
  // 0,02 rad/s dao mais de 1 grau por segundo, 68 graus em um minuto.
  //
  // O vies NAO e medido uma vez no boot e esquecido, de proposito: ele
  // anda com a temperatura, e o boot e justamente quando o chip esta frio.
  // Em vez disso, toda leitura em repouso refina a estimativa. Isso tambem
  // resolve o caso do robo que liga ja em movimento (empurrado, ou reset
  // no meio de uma missao): yawValido() so responde true quando houver
  // repouso suficiente para o vies valer alguma coisa.
  void _integrarYaw(float gyroZ_rad_s, float dt_s) {
    // dt zero = primeira leitura (nao ha intervalo ainda). dt grande =
    // houve pausa longa (reset da serial, loop travado, robo dormindo);
    // integrar um intervalo desses com a taxa de AGORA inventaria um giro
    // que ninguem viu, entao o quadro e descartado.
    if (dt_s <= 0.0f || dt_s > 0.5f) return;

    float taxa_rad_s = gyroZ_rad_s - _viesZ;

    if (fabs(taxa_rad_s) < LIMIAR_REPOUSO_RAD_S) {
      // Parado: refina o vies e NAO integra. Nao integrar em repouso e o
      // que impede a deriva de existir enquanto o robo esta parado - que e
      // a maior parte do tempo dele.
      _viesZ += (gyroZ_rad_s - _viesZ) * FATOR_APRENDIZADO_VIES;
      if (_amostrasVies < AMOSTRAS_VIES_MINIMAS) _amostrasVies++;
      return;
    }

    _yawGraus += degrees(taxa_rad_s) * dt_s;
    // normaliza para 0..360 (fmod sozinho devolve negativo para entrada
    // negativa, e o consumidor espera rumo, nao angulo com sinal)
    _yawGraus = fmod(_yawGraus, 360.0f);
    if (_yawGraus < 0.0f) _yawGraus += 360.0f;
  }

  // Le a calibracao gravada; se nao houver, fica no eixo Z puro (0,0,1).
  void carregarCalibracao() {
    CalibracaoImu dados;
    EEPROM.get(EEPROM_ENDERECO_IMU, dados);
    if (dados.magico != EEPROM_MAGICO_IMU) return;

    float norma = sqrt(dados.refX * dados.refX + dados.refY * dados.refY +
                       dados.refZ * dados.refZ);
    if (norma < 0.5f || norma > 1.5f) return;  // EEPROM corrompida - ignora

    _refX = dados.refX / norma;
    _refY = dados.refY / norma;
    _refZ = dados.refZ / norma;
    _calibrado = true;
  }

  void carregarLimiarImpacto() {
    LimiarImpactoGravado dados;
    EEPROM.get(EEPROM_ENDERECO_LIMIAR, dados);
    if (dados.magico != EEPROM_MAGICO_LIMIAR) return;
    if (!(dados.limiteG > 1.05f) || dados.limiteG > 7.5f) return;  // corrompido
    _limiteImpactoG = dados.limiteG;
  }

  Adafruit_MPU6050 _mpu;
  bool _conectado = false;
  bool _calibrado = false;
  float _inclinacaoGraus = 0;
  float _aceleracaoG = 0;  // magnitude atual em G (1,0 = parado, so gravidade)
  float _picoG = 0;        // maior magnitude desde a ultima leitura
  float _limiteImpactoG = LIMITE_IMPACTO_G;  // ajustavel; ver definirLimiteImpacto
  unsigned long _instanteImpacto = 0;  // millis() do ultimo tranco (0 = nenhum)
  float _ax = 0, _ay = 0, _az = 0;      // ultima leitura crua (m/s^2)
  float _refX = 0, _refY = 0, _refZ = 1;  // referencia "para baixo" (normalizada)

  // rumo relativo (yaw) integrado do giroscopio - ver _integrarYaw
  float _yawGraus = 0.0f;
  float _viesZ = 0.0f;             // offset do giroscopio em repouso (rad/s)
  uint16_t _amostrasVies = 0;      // quantas leituras em repouso ja entraram
  unsigned long _ultimaLeituraUs = 0;  // para o dt real da integracao
};

}  // namespace orion
