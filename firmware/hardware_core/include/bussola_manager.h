// Bussola Manager (Cap 10 secao 7) - HMC5883L via I2C (GPIO 21/22 no ESP32).
//
// *** TROCA DE MODULO EM 2026-08-15. ***
// Ate aqui este arquivo falava com um QMC6310 (endereco 0x1C). O modulo foi
// substituido por uma placa GY-271 vendida como HMC5883L. O problema: boa
// parte das GY-271 "HMC5883L" do mercado vem com um CHIP CLONE, o QMC5883L -
// outro endereco (0x0D), outros registradores, outra ordem de bytes. Por
// fora as placas sao identicas; so o barramento distingue. Por isso o
// iniciar() faz AUTODETECCAO: tenta o HMC5883L de verdade (0x1E, identidade
// "H43") e cai para o QMC5883L (0x0D) se nao achar. A varredura de I2C do
// boot (escanearI2c em main.cpp) mostra na telemetria qual dos dois esta la.
//
// O que NAO mudou com a troca (e por que):
//
//   1. Nenhum Serial.print. A serial e exclusiva do protocolo com o
//      Raspberry - um print aqui corromperia o enquadramento binario.
//   2. Calibracao NAO BLOQUEANTE. Girar 25 s dentro de um `while` seria
//      reset garantido pelo watchdog de 2 s armado em main.cpp. Continua
//      maquina de estados: quem pede volta na hora, e cada volta do loop
//      acumula um pouco.
//   3. EEPROM.commit() OBRIGATORIO no ESP32 (flash emulando EEPROM). Sem ele
//      a calibracao some no proximo reset, sem erro nenhum.
//   4. O MAGICO da EEPROM mudou (0x6310 -> 0x5883) DE PROPOSITO: a
//      calibracao gravada pelo QMC6310 nao serve para o chip novo (offsets e
//      escalas sao de outro sensor, em outra escala). O magico novo faz a
//      calibracao velha ser ignorada em vez de aplicada caladamente.
//
// A compensacao de inclinacao foi REMOVIDA em 2026-08-18: ela reaproveitava
// o acelerometro da MPU6050, que foi descartada desta placa. Sem ela, o rumo
// so vale com o robo perfeitamente nivelado (plano X/Y) - numa rampa ele
// erra. O custo foi aceito: era um comportamento de bancada, e a odometria
// de rotacao se apoia no rumo com o robo nivelado.
//
// TENSAO: o I2C do ESP32 e 3,3 V nativo, exatamente o nivel do HMC5883L.
// Ligacao direta, sem level shifter. A placa GY-271 costuma ter regulador
// proprio e aceitar 5 V no VCC, mas aqui o VCC vai no 3V3 do ESP32 - um
// caminho so, sem depender do regulador da plaquinha.
#pragma once

#include <Arduino.h>
#include <EEPROM.h>
#include <Wire.h>
#include <math.h>

namespace orion {

// --- Os dois chips que esta plaquinha pode ter ----------------------------

// HMC5883L verdadeiro (Honeywell). Endereco fixo de fabrica.
constexpr uint8_t HMC5883L_ENDERECO = 0x1E;
// Registradores de identidade: leem 'H', '4', '3' em 0x0A/0x0B/0x0C. E o
// teste que separa o HMC de verdade do clone - o clone nao tem esses
// registradores.
constexpr uint8_t HMC5883L_REG_ID_A = 0x0A;
constexpr uint8_t HMC5883L_ID_ESPERADA_0 = 'H';
constexpr uint8_t HMC5883L_ID_ESPERADA_1 = '4';
constexpr uint8_t HMC5883L_ID_ESPERADA_2 = '3';
constexpr uint8_t HMC5883L_REG_CFG_A = 0x00;     // media de 8 amostras, 15 Hz
constexpr uint8_t HMC5883L_REG_CFG_B = 0x01;     // ganho
constexpr uint8_t HMC5883L_REG_MODO = 0x02;      // continuo / single-shot
constexpr uint8_t HMC5883L_REG_DADOS = 0x03;     // 6 bytes: X, Z, Y (!)
constexpr uint8_t HMC5883L_REG_STATUS = 0x09;    // bit0 = RDY
// Ganho +-1,3 Gauss = 1090 LSB/Gauss (default de datasheet). O campo da
// Terra fica entre 0,25 e 0,65 Gauss - sobra margem mesmo com o hard-iron
// do robo somado.
constexpr float HMC5883L_LSB_POR_GAUSS = 1090.0f;

// QMC5883L (QST) - o clone que vem em muita GY-271 "HMC5883L".
constexpr uint8_t QMC5883L_ENDERECO = 0x0D;
constexpr uint8_t QMC5883L_REG_DADOS = 0x00;     // 6 bytes: X, Y, Z (little-endian)
constexpr uint8_t QMC5883L_REG_STATUS = 0x06;    // bit0 = DRDY
constexpr uint8_t QMC5883L_REG_CTRL1 = 0x09;
constexpr uint8_t QMC5883L_REG_SET_RESET = 0x0B;
// CTRL1 = 0x1D: modo continuo, 200 Hz, escala +-8 Gauss, oversampling 512.
// A escala larga perde resolucao mas nao satura nunca - e o mesmo numero
// que as bibliotecas conhecidas usam, ou seja, validado pelo mercado inteiro.
constexpr float QMC5883L_LSB_POR_GAUSS = 3000.0f;  // escala +-8 Gauss

// A bussola nao precisa ser lida todo loop: o campo magnetico nao muda em
// milissegundos, e cada leitura sao duas transacoes I2C que entram na volta
// do loop - e a volta do loop e justamente o que faz o eco do ultrassom ser
// perdido (ver a instrumentacao loop_max_us em main.cpp). 100 ms da 10
// leituras por segundo, de sobra para medir um giro.
constexpr unsigned long INTERVALO_LEITURA_BUSSOLA_MS = 100;

// RUMO VELHO NAO E RUMO (2026-11-10, auditoria C2-03). Quando o I2C falha
// (fio solto, chip travado), `_lerCampo` devolve false e o atualizar()
// mantem o rumo anterior - e `rumoValido()` continuava true PARA SEMPRE com
// um numero congelado. A malha da reta (motor_manager.h) e a odometria do Pi
// confiavam nele: medido na auditoria, um giro de 90 graus virava ~2 graus
// em 6 s, porque o rumo parado "corrigia" o encoder que via o giro. 500 ms
// = 5 leituras perdidas seguidas (a 10 Hz): uma falha isolada passa, um
// barramento mudo derruba o rumo para "nao sei" - e ai o encoder segue
// sozinho, que e o que ele ja sabe fazer.
constexpr unsigned long IDADE_MAXIMA_RUMO_MS = 500;

// Quanto tempo a calibracao fica coletando min/max enquanto o robo gira.
constexpr unsigned long DURACAO_CALIBRACAO_BUSSOLA_MS = 25000;

// Amplitude minima aceitavel por eixo, EM GAUSS, para a calibracao valer.
// Abaixo disso o eixo praticamente nao girou e a direcao sairia torta -
// melhor recusar do que gravar uma calibracao ruim por cima de uma boa.
// Em Gauss e nao em LSB porque cada chip tem sua escala: convertido para
// LSB na hora de usar, com o _lsbPorGauss do modelo detectado. (O valor
// antigo, 300 LSB no QMC6310 a 3750 LSB/Gauss, equivalia a estes 0,08 G.)
constexpr float AMPLITUDE_MINIMA_CALIBRACAO_GAUSS = 0.08f;

// EEPROM: a bussola e agora o UNICO usuario de EEPROM do firmware (a
// MPU6050 saiu em 2026-08-18 e levou os enderecos 0 e 32). 64 fica folgado.
constexpr int EEPROM_ENDERECO_BUSSOLA = 64;
// Magico NOVO (era 0x6310): invalida de proposito a calibracao do chip
// anterior - ver o item 4 do cabecalho.
// 0x5884 (2026-10-31): a struct ganhou eixos e sinal - a calibracao antiga
// (so X/Y fixos) nao vale mais.
constexpr uint16_t EEPROM_MAGICO_BUSSOLA = 0x5884;

// Amostras guardadas durante a calibracao (10 Hz x 25 s = 250). Servem para
// descobrir, DEPOIS de achar o centro e a escala, para que lado o rumo anda
// enquanto o robo gira - e assim o sinal da montagem (2026-10-31).
constexpr int MAX_AMOSTRAS_CALIBRACAO = 300;

// ALINHAMENTO DOS EIXOS <<< AJUSTAR QUANDO MONTAR NO FOFAO.
// O rumo usa o plano X/Y do chip: "norte" esta onde o eixo X apontar
// quando o robo estiver nivelado. Se a plaquinha estiver montada virada
// em relacao ao chassi, o rumo sai defasado - ver os sinais de montagem
// na hora da instalacao.

// Calibracao gravada na EEPROM.
//
// Girando para todos os lados, a ponta do vetor do campo magnetico deveria
// desenhar uma ESFERA centrada no zero. Na pratica ela desenha uma esfera
// torta e fora do lugar, por causa do ferro e dos imas por perto. O offset
// traz o centro de volta ao zero (hard-iron); a escala desentorta os eixos
// achatados (soft-iron).
//
// EIXOS E SINAL DESCOBERTOS NA CALIBRACAO (2026-10-31). O rumo usava sempre
// o plano X/Y do chip, e o modulo no robo nao esta deitado com X/Y na
// horizontal: girando o robo em 8 paradas de 45 graus, o rumo so pulava
// entre ~75 e ~270 - um dos eixos apontava para cima e media o campo
// vertical, que nao muda com o giro. Agora a calibracao escolhe os DOIS
// eixos que mais variam no giro (os horizontais, qualquer que seja a
// posicao do modulo) e, se o robo girou num sentido conhecido, descobre se
// o rumo sobe ou desce com ele (modulo de cabeca para baixo inverte).
struct CalibracaoBussola {
  uint16_t magico;
  float offsetX, offsetY, offsetZ;
  float escalaX, escalaY, escalaZ;
  uint8_t eixoA, eixoB;  // 0 = X, 1 = Y, 2 = Z: o plano horizontal
  int8_t sinal;          // +1: rumo cresce no sentido horario; -1: inverso
};

class BussolaManager {
 public:
  // Wire.begin() ja foi chamado por main.cpp - aqui so detectamos e
  // configuramos o chip. Retorna false se nenhum dos dois modelos
  // responder, sem travar nada (Cap 6 s.8).
  bool iniciar() {
    if (_iniciarHmc5883l()) {
      _endereco = HMC5883L_ENDERECO;
      _lsbPorGauss = HMC5883L_LSB_POR_GAUSS;
      _ehCloneQmc = false;
    } else if (_iniciarQmc5883l()) {
      _endereco = QMC5883L_ENDERECO;
      _lsbPorGauss = QMC5883L_LSB_POR_GAUSS;
      _ehCloneQmc = true;
    } else {
      _conectado = false;
      return false;
    }
    _conectado = true;
    _carregarCalibracao();
    return true;
  }

  // Chamar a cada loop(); ela mesma respeita INTERVALO_LEITURA_BUSSOLA_MS.
  void atualizar() {
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
    float m[3];
    _corrigir(bx, by, bz, _cal, m);

    // Intensidade do campo ja calibrado, em microtesla. Este numero deveria
    // ficar PARADO por mais que o robo gire - e o melhor teste de qualidade
    // que existe. Se ele oscila muito, a calibracao esta ruim ou tem motor
    // ligado perto demais.
    _campoUt = sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]) / _lsbPorGauss * 100.0f;

    // Compensacao de inclinacao REMOVIDA (2026-08-18): ela usava o
    // acelerometro da MPU6050, que foi descartada desta placa. Sem ela, o
    // rumo so vale com o robo nivelado - no plano que a calibracao achou.
    float graus = _rumoDe(m, _cal);
    _rumoGraus = graus;
    _temLeitura = true;
    _ultimaLeituraBoaMs = agora;
  }

  // Comeca a coleta de min/max. Quem chama volta na hora - a coleta acontece
  // ao longo das proximas voltas do loop(), enquanto o robo gira. Recusa se
  // o modulo nao estiver conectado ou se ja houver uma em andamento.
  // `girandoHorario`: o robo vai girar no sentido horario durante a coleta
  // (CALIBRATE_COMPASS com "girar"). So assim da para descobrir o SINAL da
  // montagem - girando a mao, o sentido e desconhecido e fica +1.
  bool iniciarCalibracao(bool girandoHorario = false) {
    if (!_conectado || _calibrando) return false;
    _girandoHorario = girandoHorario;
    _nAmostras = 0;
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
  // o offset de hard-iron pode ser maior que o proprio campo da Terra. E so
  // vale FRESCO: leitura boa mais velha que IDADE_MAXIMA_RUMO_MS = rumo
  // congelado, que e pior que rumo nenhum (C2-03, 2026-11-10).
  bool rumoValido() const {
    return _conectado && _calibrada && _temLeitura && !_calibrando &&
           (millis() - _ultimaLeituraBoaMs) <= IDADE_MAXIMA_RUMO_MS;
  }
  float rumoGraus() const { return _rumoGraus; }
  float campoUt() const { return _campoUt; }
  //: Eixos usados no rumo ("XY", "YZ"...) - diagnostico da montagem.
  const char* eixosRumo() const {
    static const char* NOMES[3][3] = {{"", "XY", "XZ"}, {"YX", "", "YZ"}, {"ZX", "ZY", ""}};
    return NOMES[_cal.eixoA % 3][_cal.eixoB % 3];
  }
  //: Quanto o rumo andou na ultima calibracao girando (graus, com o sinal ja
  //: aplicado). Uma volta inteira ou mais = coleta boa.
  float voltaCalibracaoGraus() const { return _voltaCalibracaoGraus; }
  // Quanto falta da calibracao em andamento, em ms (0 se nao ha nenhuma).
  unsigned long restanteCalibracaoMs() const {
    if (!_calibrando) return 0;
    unsigned long decorrido = millis() - _inicioCalibracaoMs;
    return decorrido >= DURACAO_CALIBRACAO_BUSSOLA_MS
               ? 0
               : DURACAO_CALIBRACAO_BUSSOLA_MS - decorrido;
  }

 private:
  // --- Deteccao e configuracao de cada chip -------------------------------

  bool _iniciarHmc5883l() {
    // A identidade "H43" e a unica prova confiavel de que e um HMC5883L de
    // verdade - endereco sozinho nao basta, porque qualquer ruido no
    // barramento pode dar ACK onde nao devia.
    uint8_t id[3] = {0, 0, 0};
    _endereco = HMC5883L_ENDERECO;
    if (!_ler(HMC5883L_REG_ID_A, id, 3)) return false;
    if (id[0] != HMC5883L_ID_ESPERADA_0 || id[1] != HMC5883L_ID_ESPERADA_1 ||
        id[2] != HMC5883L_ID_ESPERADA_2) {
      return false;
    }
    _escrever(HMC5883L_REG_CFG_A, 0x70);  // 8 amostras de media, 15 Hz
    _escrever(HMC5883L_REG_CFG_B, 0x20);  // +-1,3 Gauss (1090 LSB/Gauss)
    _escrever(HMC5883L_REG_MODO, 0x00);   // modo continuo
    return true;
  }

  bool _iniciarQmc5883l() {
    // O clone nao tem registrador de identidade confiavel (o que o datasheet
    // chama de ID le 0xFF, constante que nao distingue nada). A prova aqui e
    // mais fraca: o endereco 0x0D respondeu. Como o HMC foi testado antes e
    // falhou, se ALGUEM responde em 0x0D so pode ser o clone - nenhum outro
    // modulo do robo usa esse endereco.
    _endereco = QMC5883L_ENDERECO;
    Wire.beginTransmission(_endereco);
    if (Wire.endTransmission() != 0) return false;
    _escrever(QMC5883L_REG_SET_RESET, 0x01);  // periodo de set/reset (datasheet)
    _escrever(QMC5883L_REG_CTRL1, 0x1D);      // continuo, 200 Hz, +-8 Gauss
    return true;
  }

  // --- Leitura crua --------------------------------------------------------

  void _escrever(uint8_t reg, uint8_t valor) {
    Wire.beginTransmission(_endereco);
    Wire.write(reg);
    Wire.write(valor);
    Wire.endTransmission();
  }

  bool _ler(uint8_t reg, uint8_t* destino, uint8_t quantos) {
    Wire.beginTransmission(_endereco);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(_endereco, quantos) != quantos) return false;
    for (uint8_t i = 0; i < quantos; i++) destino[i] = Wire.read();
    return true;
  }

  bool _lerCampo(int16_t& x, int16_t& y, int16_t& z) {
    uint8_t status;
    uint8_t b[6];

    if (_ehCloneQmc) {
      if (!_ler(QMC5883L_REG_STATUS, &status, 1)) return false;
      if (!(status & 0x01)) return false;  // DRDY: ainda nao ha dado novo
      if (!_ler(QMC5883L_REG_DADOS, b, 6)) return false;
      // QMC5883L: byte MENOS significativo primeiro, na ordem X, Y, Z.
      x = (int16_t)(b[0] | (b[1] << 8));
      y = (int16_t)(b[2] | (b[3] << 8));
      z = (int16_t)(b[4] | (b[5] << 8));
      return true;
    }

    if (!_ler(HMC5883L_REG_STATUS, &status, 1)) return false;
    if (!(status & 0x01)) return false;  // RDY: ainda nao ha dado novo
    if (!_ler(HMC5883L_REG_DADOS, b, 6)) return false;
    // HMC5883L: byte MAIS significativo primeiro (big-endian) e, ARMADILHA
    // CLASSICA, na ordem X, Z, Y - nao X, Y, Z. Ler na ordem errada troca
    // o eixo vertical com um horizontal: parado e nivelado parece certo, e
    // a direcao erra feio ao inclinar. Erro silencioso, so se pega na rua.
    x = (int16_t)((b[0] << 8) | b[1]);
    z = (int16_t)((b[2] << 8) | b[3]);
    y = (int16_t)((b[4] << 8) | b[5]);
    return true;
  }

  // --- Calibracao (inalterada na troca de chip) -----------------------------

  void _acumularCalibracao(int16_t x, int16_t y, int16_t z) {
    if (x < _minX) _minX = x;
    if (x > _maxX) _maxX = x;
    if (y < _minY) _minY = y;
    if (y > _maxY) _maxY = y;
    if (z < _minZ) _minZ = z;
    if (z > _maxZ) _maxZ = z;
    if (_nAmostras < MAX_AMOSTRAS_CALIBRACAO) {
      _amostras[_nAmostras][0] = x;
      _amostras[_nAmostras][1] = y;
      _amostras[_nAmostras][2] = z;
      _nAmostras++;
    }
  }

  static void _corrigir(int16_t x, int16_t y, int16_t z, const CalibracaoBussola& c, float m[3]) {
    m[0] = (x - c.offsetX) * c.escalaX;
    m[1] = (y - c.offsetY) * c.escalaY;
    m[2] = (z - c.offsetZ) * c.escalaZ;
  }

  static float _rumoDe(const float m[3], const CalibracaoBussola& c) {
    float graus = atan2(-m[c.eixoB], m[c.eixoA]) * 180.0f / PI * c.sinal;
    graus = fmodf(graus, 360.0f);
    return graus < 0 ? graus + 360.0f : graus;
  }

  void _fecharCalibracao() {
    _calibrando = false;

    float ampX = (_maxX - _minX) / 2.0f;
    float ampY = (_maxY - _minY) / 2.0f;
    float ampZ = (_maxZ - _minZ) / 2.0f;

    // Limiar convertido para a escala do chip detectado (ver
    // AMPLITUDE_MINIMA_CALIBRACAO_GAUSS).
    float amplitudeMinima = AMPLITUDE_MINIMA_CALIBRACAO_GAUSS * _lsbPorGauss;

    // Os dois eixos que MAIS variaram no giro sao o plano horizontal.
    float amp[3] = {ampX, ampY, ampZ};
    uint8_t menor = 0;
    if (amp[1] < amp[menor]) menor = 1;
    if (amp[2] < amp[menor]) menor = 2;
    uint8_t a = menor == 0 ? 1 : 0;
    uint8_t b = menor == 2 ? 1 : 2;
    if (amp[a] < amplitudeMinima || amp[b] < amplitudeMinima) {
      _ultimaCalibracaoOk = false;
      return;
    }

    CalibracaoBussola nova;
    nova.magico = EEPROM_MAGICO_BUSSOLA;
    nova.offsetX = (_maxX + _minX) / 2.0f;
    nova.offsetY = (_maxY + _minY) / 2.0f;
    nova.offsetZ = (_maxZ + _minZ) / 2.0f;
    float media = (amp[a] + amp[b]) / 2.0f;
    // Eixo que quase nao variou (o vertical) fica com escala 1: dividir pela
    // amplitude dele inventaria um campo enorme a partir de ruido.
    nova.escalaX = ampX >= amplitudeMinima ? media / ampX : 1.0f;
    nova.escalaY = ampY >= amplitudeMinima ? media / ampY : 1.0f;
    nova.escalaZ = ampZ >= amplitudeMinima ? media / ampZ : 1.0f;
    nova.eixoA = a;
    nova.eixoB = b;
    nova.sinal = 1;

    // SINAL: refaz o rumo de cada amostra com a calibracao nova e soma o
    // quanto ele andou. O robo girou no sentido horario; se o rumo DESCEU,
    // o modulo esta invertido (de cabeca para baixo, ou eixos trocados).
    float volta = 0.0f;
    for (int i = 1; i < _nAmostras; i++) {
      float m0[3], m1[3];
      _corrigir(_amostras[i - 1][0], _amostras[i - 1][1], _amostras[i - 1][2], nova, m0);
      _corrigir(_amostras[i][0], _amostras[i][1], _amostras[i][2], nova, m1);
      float d = _rumoDe(m1, nova) - _rumoDe(m0, nova);
      if (d > 180.0f) d -= 360.0f;
      if (d < -180.0f) d += 360.0f;
      volta += d;
    }
    if (_girandoHorario && volta < 0.0f) {
      nova.sinal = -1;
      volta = -volta;
    }
    _voltaCalibracaoGraus = volta;
    _cal = nova;
    EEPROM.put(EEPROM_ENDERECO_BUSSOLA, _cal);
    // commit() OBRIGATORIO no ESP32 - ver o item 3 do cabecalho deste
    // arquivo. Sem ele os 25 s de giro que o dono acabou de fazer com o
    // robo na mao se perdem no proximo reset, caladamente.
    EEPROM.commit();
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
    if (dados.eixoA > 2 || dados.eixoB > 2 || dados.eixoA == dados.eixoB) return;
    if (dados.sinal != 1 && dados.sinal != -1) return;
    _cal = dados;
    _calibrada = true;
  }

  uint8_t _endereco = HMC5883L_ENDERECO;
  float _lsbPorGauss = HMC5883L_LSB_POR_GAUSS;
  bool _ehCloneQmc = false;

  bool _conectado = false;
  bool _calibrada = false;
  bool _calibrando = false;
  bool _temLeitura = false;
  bool _ultimaCalibracaoOk = false;

  float _rumoGraus = 0.0f;
  float _campoUt = 0.0f;

  unsigned long _ultimaLeituraMs = 0;
  //: millis() da ultima leitura que virou rumo (I2C ok, dado novo, fora da
  //: calibracao). E ela, e nao _ultimaLeituraMs (que marca a TENTATIVA), que
  //: diz se o rumo ainda vale.
  unsigned long _ultimaLeituraBoaMs = 0;
  unsigned long _inicioCalibracaoMs = 0;

  int16_t _minX = 0, _maxX = 0, _minY = 0, _maxY = 0, _minZ = 0, _maxZ = 0;

  bool _girandoHorario = false;
  int _nAmostras = 0;
  int16_t _amostras[MAX_AMOSTRAS_CALIBRACAO][3];
  float _voltaCalibracaoGraus = 0.0f;

  // Sem calibracao: offset zero e escala 1 = o valor cru, que so serve para
  // ver se o sensor esta vivo, nunca para navegar.
  CalibracaoBussola _cal{0, 0.0f, 0.0f, 0.0f, 1.0f, 1.0f, 1.0f, 0, 1, 1};
};

}  // namespace orion
