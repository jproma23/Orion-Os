// Bussola Manager (Cap 10 secao 7) - QMC6310 via I2C (pinos 20/21), no
// MESMO barramento da MPU6050.
//
// Portado do projeto de bancada `~/Desktop/bussola_orion` (validado em
// 2026-07-27 numa WeMos D1 R1: ~25 uT de amplitude, contra ~23 uT reais do
// Brasil). Tres coisas MUDARAM na portagem, e todas por exigencia deste
// firmware:
//
//   1. Nenhum Serial.print. A serial dos pinos 0/1 e exclusiva do protocolo
//      com o Raspberry - um print aqui corromperia o enquadramento binario.
//   2. Calibracao NAO BLOQUEANTE. O original girava 25 s dentro de um
//      `while` com delay(20); aqui isso seria reset garantido, porque o
//      `wdt_enable(WDTO_2S)` de main.cpp reinicia o chip se o loop() nao
//      chamar wdt_reset() por 2 s. Virou maquina de estados: quem pede a
//      calibracao volta na hora, e cada volta do loop acumula um pouco.
//   3. EEPROM sem `commit()`. Aquilo e coisa de ESP8266 (flash emulando
//      EEPROM); no AVR a escrita e direta.
//
// A compensacao de inclinacao reaproveita o acelerometro que a ImuManager JA
// le a cada 50 ms - nao ha segunda leitura I2C da MPU6050 aqui. Sem essa
// compensacao a direcao so vale com o robo perfeitamente nivelado; numa
// rampa ela erra feio.
//
// ATENCAO A TENSAO (nao validado no Mega ate 2026-07-27): o QMC6310 e um
// componente de 3,3 V e foi testado numa WeMos, que tem I2C de 3,3 V. O
// I2C do Mega e de 5 V e o Wire.begin() ainda liga pull-ups internos para
// 5 V. Conferir se a plaquinha tem regulador + level shifter embutidos
// ANTES de ligar nos pinos 20/21, ou usar um conversor de nivel. Enquanto
// isso nao for conferido, este modulo simplesmente reporta
// `conectado() == false` e nada mais acontece (Cap 6 s.8).
#pragma once

#include <Arduino.h>
#include <EEPROM.h>
#include <Wire.h>
#include <math.h>

namespace orion {

constexpr uint8_t BUSSOLA_ENDERECO = 0x1C;
constexpr uint8_t BUSSOLA_CHIPID_ESPERADO = 0x80;

// Registradores do QMC6310 (datasheet QST Rev. C).
constexpr uint8_t BUSSOLA_REG_CHIPID = 0x00;
constexpr uint8_t BUSSOLA_REG_DADOS = 0x01;
constexpr uint8_t BUSSOLA_REG_STATUS = 0x09;
constexpr uint8_t BUSSOLA_REG_CTRL1 = 0x0A;
constexpr uint8_t BUSSOLA_REG_CTRL2 = 0x0B;
constexpr uint8_t BUSSOLA_REG_SINAL = 0x29;

constexpr float BUSSOLA_LSB_POR_GAUSS = 3750.0f;  // escala +-8 Gauss

// A bussola nao precisa ser lida todo loop: o campo magnetico nao muda em
// milissegundos, e cada leitura sao duas transacoes I2C que entram na volta
// do loop - e a volta do loop e justamente o que faz o eco do ultrassom ser
// perdido (ver a instrumentacao loop_max_us em main.cpp). 100 ms da 10
// leituras por segundo, de sobra para medir um giro.
constexpr unsigned long INTERVALO_LEITURA_BUSSOLA_MS = 100;

// Quanto tempo a calibracao fica coletando min/max enquanto o robo gira.
constexpr unsigned long DURACAO_CALIBRACAO_BUSSOLA_MS = 25000;

// Amplitude minima aceitavel por eixo para a calibracao valer. Abaixo disso
// o eixo praticamente nao girou e a direcao sairia torta - melhor recusar do
// que gravar uma calibracao ruim por cima de uma boa.
constexpr int AMPLITUDE_MINIMA_CALIBRACAO = 300;

// EEPROM: a ImuManager ja usa o endereco 0 (calibracao, 14 bytes) e o 32
// (limiar de impacto, 6 bytes). 64 fica bem longe dos dois.
constexpr int EEPROM_ENDERECO_BUSSOLA = 64;
constexpr uint16_t EEPROM_MAGICO_BUSSOLA = 0x6310;

// ALINHAMENTO DOS EIXOS <<< AJUSTAR QUANDO MONTAR NO FOFAO.
// A compensacao de inclinacao so funciona se a bussola e o acelerometro
// concordarem sobre onde e X, Y e Z. Sao duas plaquinhas separadas, entao
// elas podem acabar montadas viradas uma em relacao a outra. Sintoma de
// estar errado: inclinar o robo faz a direcao pular loucamente (parado ela
// parece certa). Correcao: trocar os sinais abaixo para -1.0f, um de cada
// vez, ate a direcao ficar estavel ao inclinar.
constexpr float BUSSOLA_SINAL_ACEL_X = 1.0f;
constexpr float BUSSOLA_SINAL_ACEL_Y = 1.0f;
constexpr float BUSSOLA_SINAL_ACEL_Z = 1.0f;

// Calibracao gravada na EEPROM.
//
// Girando para todos os lados, a ponta do vetor do campo magnetico deveria
// desenhar uma ESFERA centrada no zero. Na pratica ela desenha uma esfera
// torta e fora do lugar, por causa do ferro e dos imas por perto. O offset
// traz o centro de volta ao zero (hard-iron); a escala desentorta os eixos
// achatados (soft-iron).
struct CalibracaoBussola {
  uint16_t magico;
  float offsetX, offsetY, offsetZ;
  float escalaX, escalaY, escalaZ;
};

class BussolaManager {
 public:
  // Wire.begin() ja foi chamado por main.cpp (junto com o setWireTimeout) -
  // aqui so configuramos o chip. Retorna false se o modulo nao responder,
  // sem travar nada (Cap 6 s.8).
  bool iniciar() {
    uint8_t id = 0;
    if (!_ler(BUSSOLA_REG_CHIPID, &id, 1) || id != BUSSOLA_CHIPID_ESPERADO) {
      _conectado = false;
      return false;
    }
    _escrever(BUSSOLA_REG_SINAL, 0x06);  // sinal dos eixos
    _escrever(BUSSOLA_REG_CTRL2, 0x08);  // +-8 Gauss, Set/Reset ligado
    _escrever(BUSSOLA_REG_CTRL1, 0xC3);  // modo continuo
    _conectado = true;
    _carregarCalibracao();
    return true;
  }

  // Chamar a cada loop(); ela mesma respeita INTERVALO_LEITURA_BUSSOLA_MS.
  // `ax/ay/az` sao a leitura crua do acelerometro da ImuManager (m/s^2) -
  // so a DIRECAO importa, a magnitude e normalizada aqui dentro.
  void atualizar(float ax, float ay, float az) {
    if (!_conectado) return;

    unsigned long agora = millis();
    if (agora - _ultimaLeituraMs < INTERVALO_LEITURA_BUSSOLA_MS) return;
    _ultimaLeituraMs = agora;

    int16_t bx, by, bz;
    if (!_lerCampo(bx, by, bz)) return;  // sem dado novo - mantem o anterior

    if (_calibrando) {
      _acumularCalibracao(bx, by, bz);
      if (agora - _inicioCalibracaoMs >= DURACAO_CALIBRACAO_BUSSOLA_MS) {
        _fecharCalibracao();
      }
      return;  // durante a calibracao o rumo nao vale nada
    }

    // Aplica a calibracao: tira o deslocamento e desentorta a escala.
    float mx = (bx - _cal.offsetX) * _cal.escalaX;
    float my = (by - _cal.offsetY) * _cal.escalaY;
    float mz = (bz - _cal.offsetZ) * _cal.escalaZ;

    // Intensidade do campo ja calibrado, em microtesla. Este numero deveria
    // ficar PARADO por mais que o robo gire - e o melhor teste de qualidade
    // que existe. Se ele oscila muito, a calibracao esta ruim ou tem motor
    // ligado perto demais.
    _campoUt = sqrt(mx * mx + my * my + mz * mz) / BUSSOLA_LSB_POR_GAUSS * 100.0f;

    float axs = ax * BUSSOLA_SINAL_ACEL_X;
    float ays = ay * BUSSOLA_SINAL_ACEL_Y;
    float azs = az * BUSSOLA_SINAL_ACEL_Z;
    float normaAcel = sqrt(axs * axs + ays * ays + azs * azs);

    float graus;
    if (normaAcel > 0.001f) {
      // O acelerometro parado sente a gravidade, que sempre aponta para
      // baixo - comparando os eixos dele da para saber o quanto a placa esta
      // torta. Com isso "endireitamos" o vetor magnetico de volta para o
      // plano horizontal, como se a bussola estivesse deitada mesmo com o
      // robo inclinado (formula classica de tilt compensation).
      float rolagem = atan2(ays, azs);
      float arfagem = atan2(-axs, sqrt(ays * ays + azs * azs));

      float mxh = mx * cos(arfagem) + my * sin(rolagem) * sin(arfagem) +
                  mz * cos(rolagem) * sin(arfagem);
      float myh = my * cos(rolagem) - mz * sin(rolagem);
      graus = atan2(-myh, mxh) * 180.0f / PI;
    } else {
      // Sem acelerometro utilizavel: so vale com o robo nivelado.
      graus = atan2(-my, mx) * 180.0f / PI;
    }

    if (graus < 0) graus += 360.0f;
    _rumoGraus = graus;
    _temLeitura = true;
  }

  // Comeca a coleta de min/max. Quem chama volta na hora - a coleta acontece
  // ao longo das proximas voltas do loop(), enquanto o robo gira. Recusa se
  // o modulo nao estiver conectado ou se ja houver uma em andamento.
  bool iniciarCalibracao() {
    if (!_conectado || _calibrando) return false;
    _minX = _minY = _minZ = 32767;
    _maxX = _maxY = _maxZ = -32768;
    _inicioCalibracaoMs = millis();
    _calibrando = true;
    _ultimaCalibracaoOk = false;
    return true;
  }

  bool conectado() const { return _conectado; }
  bool calibrando() const { return _calibrando; }
  bool calibrada() const { return _calibrada; }
  // O rumo so significa alguma coisa com o modulo calibrado: sem calibracao
  // o offset de hard-iron pode ser maior que o proprio campo da Terra.
  bool rumoValido() const { return _conectado && _calibrada && _temLeitura && !_calibrando; }
  float rumoGraus() const { return _rumoGraus; }
  float campoUt() const { return _campoUt; }
  // Quanto falta da calibracao em andamento, em ms (0 se nao ha nenhuma).
  unsigned long restanteCalibracaoMs() const {
    if (!_calibrando) return 0;
    unsigned long decorrido = millis() - _inicioCalibracaoMs;
    return decorrido >= DURACAO_CALIBRACAO_BUSSOLA_MS
               ? 0
               : DURACAO_CALIBRACAO_BUSSOLA_MS - decorrido;
  }

 private:
  void _escrever(uint8_t reg, uint8_t valor) {
    Wire.beginTransmission(BUSSOLA_ENDERECO);
    Wire.write(reg);
    Wire.write(valor);
    Wire.endTransmission();
  }

  bool _ler(uint8_t reg, uint8_t* destino, uint8_t quantos) {
    Wire.beginTransmission(BUSSOLA_ENDERECO);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(BUSSOLA_ENDERECO, quantos) != quantos) return false;
    for (uint8_t i = 0; i < quantos; i++) destino[i] = Wire.read();
    return true;
  }

  bool _lerCampo(int16_t& x, int16_t& y, int16_t& z) {
    uint8_t status;
    if (!_ler(BUSSOLA_REG_STATUS, &status, 1)) return false;
    if (!(status & 0x01)) return false;  // bit DRDY: ainda nao ha dado novo

    uint8_t b[6];
    if (!_ler(BUSSOLA_REG_DADOS, b, 6)) return false;

    // QMC6310 entrega o byte MENOS significativo primeiro (little-endian) -
    // ao contrario da MPU6050, que e big-endian. Trocar isso e um erro
    // classico e silencioso: o valor sai plausivel, so que errado.
    x = (int16_t)(b[0] | (b[1] << 8));
    y = (int16_t)(b[2] | (b[3] << 8));
    z = (int16_t)(b[4] | (b[5] << 8));
    return true;
  }

  void _acumularCalibracao(int16_t x, int16_t y, int16_t z) {
    if (x < _minX) _minX = x;
    if (x > _maxX) _maxX = x;
    if (y < _minY) _minY = y;
    if (y > _maxY) _maxY = y;
    if (z < _minZ) _minZ = z;
    if (z > _maxZ) _maxZ = z;
  }

  void _fecharCalibracao() {
    _calibrando = false;

    float ampX = (_maxX - _minX) / 2.0f;
    float ampY = (_maxY - _minY) / 2.0f;
    float ampZ = (_maxZ - _minZ) / 2.0f;

    // SO X E Y SAO EXIGIDOS (corrigido 2026-07-27).
    //
    // A versao anterior exigia amplitude nos tres eixos, herdada do projeto
    // de bancada onde o modulo era girado na mao em todas as direcoes. Num
    // robo terrestre isso nunca acontece: ele gira em torno do proprio eixo
    // vertical, entao o Z fica praticamente constante e a calibracao seria
    // recusada no fim dos 25 s, sempre.
    //
    // Para rumo com o robo nivelado o que importa e o plano X/Y - e a
    // calibracao PRECISA ser feita com tudo montado, porque o que se quer
    // medir e justamente o ferro e os imas do proprio Fofao.
    if (ampX < AMPLITUDE_MINIMA_CALIBRACAO || ampY < AMPLITUDE_MINIMA_CALIBRACAO) {
      _ultimaCalibracaoOk = false;
      return;
    }

    _cal.magico = EEPROM_MAGICO_BUSSOLA;
    _cal.offsetX = (_maxX + _minX) / 2.0f;
    _cal.offsetY = (_maxY + _minY) / 2.0f;

    float media = (ampX + ampY) / 2.0f;
    _cal.escalaX = media / ampX;
    _cal.escalaY = media / ampY;

    // Z so e corrigido se ele REALMENTE girou (calibracao na mao, fora do
    // robo). Girando so em torno do eixo vertical, (maxZ+minZ)/2 nao e o
    // offset de hard-iron - e o proprio campo vertical da Terra somado a
    // ele. Gravar isso como offset zeraria a componente vertical real e
    // estragaria a compensacao de inclinacao. Na duvida, nao corrige.
    if (ampZ >= AMPLITUDE_MINIMA_CALIBRACAO) {
      _cal.offsetZ = (_maxZ + _minZ) / 2.0f;
      _cal.escalaZ = media / ampZ;
    } else {
      _cal.offsetZ = 0.0f;
      _cal.escalaZ = 1.0f;
    }

    EEPROM.put(EEPROM_ENDERECO_BUSSOLA, _cal);
    _calibrada = true;
    _ultimaCalibracaoOk = true;
  }

  void _carregarCalibracao() {
    CalibracaoBussola dados;
    EEPROM.get(EEPROM_ENDERECO_BUSSOLA, dados);
    if (dados.magico != EEPROM_MAGICO_BUSSOLA) return;
    // Escala zero/negativa ou absurda = EEPROM corrompida; ignora e segue
    // sem calibracao em vez de calcular rumo sobre lixo.
    if (!(dados.escalaX > 0.01f) || !(dados.escalaY > 0.01f) || !(dados.escalaZ > 0.01f)) return;
    if (dados.escalaX > 100.0f || dados.escalaY > 100.0f || dados.escalaZ > 100.0f) return;
    _cal = dados;
    _calibrada = true;
  }

  bool _conectado = false;
  bool _calibrada = false;
  bool _calibrando = false;
  bool _temLeitura = false;
  bool _ultimaCalibracaoOk = false;

  float _rumoGraus = 0.0f;
  float _campoUt = 0.0f;

  unsigned long _ultimaLeituraMs = 0;
  unsigned long _inicioCalibracaoMs = 0;

  int16_t _minX = 0, _maxX = 0, _minY = 0, _maxY = 0, _minZ = 0, _maxZ = 0;

  // Sem calibracao: offset zero e escala 1 = o valor cru, que so serve para
  // ver se o sensor esta vivo, nunca para navegar.
  CalibracaoBussola _cal{0, 0.0f, 0.0f, 0.0f, 1.0f, 1.0f, 1.0f};
};

}  // namespace orion
