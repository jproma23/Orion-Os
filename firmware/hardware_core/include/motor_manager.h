// Motor Manager (Cap 10 secao 10) - controle dos 2x NEMA17 via 2x TB6600.
//
// Gera pulsos STEP por temporizacao com micros(), sem delay() no loop
// principal (Cap 10 secao 10: "nunca bloquear"). Toda a logica de
// velocidade/aceleracao fica encapsulada aqui - o Raspberry so manda
// comandos de alto nivel (MOVE_FORWARD, MOVE_DISTANCE, ...).
//
// Conversao distancia<->passos e grau<->passos usa constantes de bancada
// (PASSOS_POR_METRO, PASSOS_POR_GRAU) - sem calibracao fisica real ainda
// (isso e a autocalibracao da Fase 12/Cap 12 s.9). Ate la, MOVE_DISTANCE e
// TURN_*  com angulo sao aproximados; TURN_* sem angulo (girar ate STOP) e
// preciso porque nao depende dessas constantes.
#pragma once

#include <Arduino.h>

#include "pins.h"

namespace orion {

// MEDIDAS FISICAS DESTA MONTAGEM (confirmadas com o mantenedor, 2026-07-29).
// Ficam explicitas para as constantes abaixo serem DERIVADAS delas, em vez de
// digitadas - numero digitado a mao foi exatamente a origem do erro corrigido
// logo abaixo.
constexpr float RODA_DIAMETRO_M = 0.200f;        // roda de 200 mm
constexpr float PASSOS_POR_VOLTA_MOTOR = 1600.0f;  // TB6600 em 1/8 (S1=OFF S2=ON S3=OFF)

// ERRO CORRIGIDO EM 2026-07-29: este valor era 4000, e a origem do engano da
// para reconstruir exatamente:
//
//     1600 / 0.4    = 4000   <- circunferencia calculada como 2 * diametro
//     1600 / 0.6283 = 2546   <- circunferencia de verdade, PI * diametro
//
// O fator entre os dois e PI/2 = 1.571, ou seja, o robo andava 57% MAIS do
// que o comandado: um "avanca 30 cm" percorria 47 cm. Erro sistematico,
// sempre no mesmo sentido, e que a odometria em malha aberta nunca corrigiria
// (ela conta o que mandou, nao o que a roda fez).
//
// Ficou mais grave em 2026-07-29 com o EDR-0027: o Mega deixou de frear por
// obstaculo, entao andar mais do que o pedido perto de uma parede nao tem
// mais rede embaixo.
//
// NAO VALIDADO NO HARDWARE AINDA - pela rubrica do mapa de saude isto e
// "em validacao" (amarelo), nao "estavel". A prova e a malha fechada por
// ultrassom: mandar andar uma distancia conhecida, medir com o sensor quanto
// andou de verdade, e comparar. Se o medido bater com 2546, confirma o 1/8;
// se voltar a dar 4000 redondo, alguem ajustou o mundo para bater com o
// numero em vez do contrario.
constexpr float PASSOS_POR_METRO = PASSOS_POR_VOLTA_MOTOR / (PI * RODA_DIAMETRO_M);

// PASSOS_POR_GRAU depende da BITOLA (distancia centro a centro das rodas),
// que ainda nao foi medida - por isso continua sendo o valor antigo por ora.
//
// A formula, girando sobre o proprio eixo (tracao diferencial): cada roda
// percorre um circulo de raio igual a metade da bitola, entao
//
//     passos_por_grau = PASSOS_POR_VOLTA_MOTOR * bitola / (360 * diametro)
//
// O PI cancela dos dois lados - o arco da roda e o perimetro dela sao ambos
// proporcionais a PI. Uma volta completa do robo = (bitola / diametro) voltas
// de roda.
//
// CONFERENCIA PENDENTE: com 1600 passos/volta e roda de 200 mm, o valor 20
// abaixo implicaria uma bitola de 0,90 m - impossivel num robo de 0,50 m de
// largura. Para uma bitola plausivel de 0,45 m o valor certo seria 10, ou
// seja, o robo estaria girando o DOBRO do angulo pedido. Medir a bitola e
// corrigir; a bussola calibrada e quem prova o resultado.
constexpr float PASSOS_POR_GRAU = 20.0f;     // SUSPEITO - ver bloco acima
constexpr long PASSOS_POR_SEGUNDO_MAXIMO = 2000;
constexpr long PASSOS_POR_SEGUNDO_MINIMO = 50;
// Testado na bancada em 2026-07-18: os TB6600 desta montagem so habilitam
// com o pino em HIGH (nao no padrao "ativo em baixo" mais comum) - motor
// ficava completamente mudo/parado (sem nem vibrar) com o valor antigo
// (true), sinal classico de driver permanentemente desabilitado.
constexpr bool ENABLE_ATIVO_EM_BAIXO = false;

// Carencia entre fechar o rele dos motores e o primeiro pulso de STEP.
//
// ACHADO DO MANTENEDOR (2026-07-27): `_configurarRoda` marcava
// `ultimoPassoUs = micros()` no MESMO instante em que `_ligarRele()` fechava o
// contato. Mas o rele quica por ~5-10 ms e o TB6600 ainda leva alguns ms para
// acordar depois de energizado - entao os primeiros pulsos de CADA movimento
// eram emitidos sem haver driver para executa-los, e o firmware contava todos
// em `passosAcumulados`.
//
// Por que isso e pior que patinacao: o erro e SISTEMATICO, sempre no mesmo
// sentido (conta mais do que andou) e sempre do mesmo tamanho. Patinacao e
// aleatoria e tende a se diluir; este erro se ACUMULA a cada arranque. Como a
// odometria e malha aberta (conta o que mandou, nao o que a roda fez), nada
// depois corrige isso.
//
// 20 ms cobre o quique do rele com folga sem ser perceptivel ao operador.
constexpr unsigned long ATRASO_RELE_US = 20000;

// Que tipo de movimento esta em curso AGORA.
//
// Existe por causa da seguranca (2026-07-29): "o robo esta se movendo" nao
// basta para decidir se um perigo frontal deve travar o motor. Avancar
// APROXIMA do obstaculo; girar no proprio eixo nao - e justamente a manobra
// de fuga. Sem esta distincao o SafetyManager so podia parar tudo, e o robo
// ficava preso de frente para o obstaculo sem poder se desvirar (decisao do
// mantenedor: "nenhum obstaculo tem q bloquear, o robo tem q desviar").
//
// A RE (movimento negativo do MOVE_DISTANCE) fica separada de FRENTE de
// proposito: ela nao tem seguranca reativa nenhuma (o SafetyManager so olha
// o radar frontal, Cap 18), entao nunca deve ganhar permissao por engano.
enum class TipoMovimento : uint8_t { NENHUM, FRENTE, RE, GIRO };

struct EstadoRoda {
  uint8_t pinoStep;
  uint8_t pinoDir;
  bool sentidoFrente = true;
  long intervaloUs = 0;      // 0 = parada
  unsigned long ultimoPassoUs = 0;
  long passosRestantes = 0;  // -1 = continuo (ate parar() ser chamado)
  long passosAcumulados = 0; // odometria simples (sem encoder)
  //: nenhum pulso antes deste instante - carencia do rele (ATRASO_RELE_US).
  unsigned long naoPulsarAntesUs = 0;
};

class MotorManager {
 public:
  void iniciar() {
    // Rele dos motores (pinos.h): escreve o nivel seguro (motores SEM
    // energia) ANTES do pinMode(OUTPUT) - evita um pulso LOW transitorio
    // (que ligaria o rele) no instante em que o pino vira saida. Mesmo
    // valor escrito duas vezes de proposito: uma enquanto ainda e INPUT
    // (pre-carrega o registrador), outra logo apos virar OUTPUT.
    digitalWrite(pinos::RELE_MOTORES, HIGH);
    pinMode(pinos::RELE_MOTORES, OUTPUT);
    digitalWrite(pinos::RELE_MOTORES, HIGH);

    pinMode(pinos::STEP_ESQUERDO, OUTPUT);
    pinMode(pinos::DIR_ESQUERDO, OUTPUT);
    pinMode(pinos::STEP_DIREITO, OUTPUT);
    pinMode(pinos::DIR_DIREITO, OUTPUT);
    pinMode(pinos::ENABLE_MOTORES, OUTPUT);

    _esquerda.pinoStep = pinos::STEP_ESQUERDO;
    _esquerda.pinoDir = pinos::DIR_ESQUERDO;
    _direita.pinoStep = pinos::STEP_DIREITO;
    _direita.pinoDir = pinos::DIR_DIREITO;

    habilitar();
  }

  void habilitar() {
    digitalWrite(pinos::ENABLE_MOTORES, ENABLE_ATIVO_EM_BAIXO ? LOW : HIGH);
  }

  void desabilitar() {
    digitalWrite(pinos::ENABLE_MOTORES, ENABLE_ATIVO_EM_BAIXO ? HIGH : LOW);
  }

  // Chamar em todo loop() - gera os pulsos STEP conforme o tempo decorrido.
  void atualizar() {
    unsigned long agora = micros();
    _atualizarRoda(_esquerda, agora);
    _atualizarRoda(_direita, agora);
  }

  void andarFrente(float velocidadePercent) {
    _ligarRele();
    _tipo = TipoMovimento::FRENTE;
    _configurarRoda(_esquerda, true, velocidadePercent, -1);
    _configurarRoda(_direita, true, velocidadePercent, -1);
  }

  void andarDistancia(float distanciaCm, float velocidadePercent) {
    _ligarRele();
    long passos = static_cast<long>(fabs(distanciaCm) * (PASSOS_POR_METRO / 100.0f));
    bool frente = distanciaCm >= 0;
    _tipo = frente ? TipoMovimento::FRENTE : TipoMovimento::RE;
    _configurarRoda(_esquerda, frente, velocidadePercent, passos);
    _configurarRoda(_direita, frente, velocidadePercent, passos);
  }

  void girarContinuo(bool sentidoHorario, float velocidadePercent) {
    _ligarRele();
    _tipo = TipoMovimento::GIRO;
    _configurarRoda(_esquerda, sentidoHorario, velocidadePercent, -1);
    _configurarRoda(_direita, !sentidoHorario, velocidadePercent, -1);
  }

  void girarGraus(float graus, bool sentidoHorario, float velocidadePercent) {
    _ligarRele();
    long passos = static_cast<long>(fabs(graus) * PASSOS_POR_GRAU);
    _tipo = TipoMovimento::GIRO;
    _configurarRoda(_esquerda, sentidoHorario, velocidadePercent, passos);
    _configurarRoda(_direita, !sentidoHorario, velocidadePercent, passos);
  }

  void parar() {
    _esquerda.intervaloUs = 0;
    _esquerda.passosRestantes = 0;
    _direita.intervaloUs = 0;
    _direita.passosRestantes = 0;
    _tipo = TipoMovimento::NENHUM;
    _desligarRele();
  }

  bool emMovimento() const {
    return _esquerda.passosRestantes != 0 || _direita.passosRestantes != 0;
  }

  //: Que movimento esta em curso. Le do estado REAL das rodas, nao so do que
  //: foi comandado: um giro por graus (`girarGraus`) termina sozinho quando
  //: os passos acabam, sem ninguem chamar parar() - se este getter confiasse
  //: so em `_tipo`, continuaria dizendo GIRO com o robo ja imovel, e a
  //: seguranca liberaria movimento apoiada num giro que ja acabou.
  TipoMovimento movimentoAtual() const {
    return emMovimento() ? _tipo : TipoMovimento::NENHUM;
  }

  //: true so quando o robo esta girando no proprio eixo (nao avanca nem re).
  bool girandoNoLugar() const { return movimentoAtual() == TipoMovimento::GIRO; }

  long passosAcumuladosEsquerda() const { return _esquerda.passosAcumulados; }
  long passosAcumuladosDireita() const { return _direita.passosAcumulados; }

 private:
  EstadoRoda _esquerda;
  EstadoRoda _direita;

  //: o que foi COMANDADO por ultimo. Nao consultar direto - usar
  //: movimentoAtual(), que cruza isto com o estado real das rodas.
  TipoMovimento _tipo = TipoMovimento::NENHUM;

  //: instante em que o rele foi fechado pela ultima vez - base da carencia.
  unsigned long _releLigadoUs = 0;

  // Rele ativo em LOW (pins.h) - liga/desliga a energia real dos TB6600.
  void _ligarRele() {
    digitalWrite(pinos::RELE_MOTORES, LOW);
    _releLigadoUs = micros();
  }
  static void _desligarRele() { digitalWrite(pinos::RELE_MOTORES, HIGH); }

  static long _intervaloParaVelocidade(float velocidadePercent) {
    velocidadePercent = constrain(velocidadePercent, 1.0f, 100.0f);
    long passosPorSegundo = static_cast<long>(
        PASSOS_POR_SEGUNDO_MINIMO +
        (PASSOS_POR_SEGUNDO_MAXIMO - PASSOS_POR_SEGUNDO_MINIMO) * (velocidadePercent / 100.0f));
    return 1000000L / passosPorSegundo;
  }

  void _configurarRoda(EstadoRoda& roda, bool sentidoFrente, float velocidadePercent,
                        long passos) {
    roda.sentidoFrente = sentidoFrente;
    digitalWrite(roda.pinoDir, sentidoFrente ? HIGH : LOW);
    roda.intervaloUs = _intervaloParaVelocidade(velocidadePercent);
    roda.passosRestantes = passos;
    roda.ultimoPassoUs = micros();
    // Todos os movimentos chamam _ligarRele() antes de chegar aqui, entao
    // _releLigadoUs e sempre desta partida.
    roda.naoPulsarAntesUs = _releLigadoUs + ATRASO_RELE_US;
  }

  static void _atualizarRoda(EstadoRoda& roda, unsigned long agora) {
    if (roda.passosRestantes == 0) return;
    // Carencia do rele: nenhum pulso enquanto o contato quica e o driver
    // acorda. Comparacao COM SINAL de proposito - `micros()` da a volta a cada
    // ~71 minutos, e uma comparacao sem sinal trataria "ainda nao chegou a
    // hora" como "ja passou faz muito tempo" na volta do contador.
    if (static_cast<long>(agora - roda.naoPulsarAntesUs) < 0) return;
    if (agora - roda.ultimoPassoUs < static_cast<unsigned long>(roda.intervaloUs)) return;

    digitalWrite(roda.pinoStep, HIGH);
    delayMicroseconds(3);  // largura minima do pulso STEP do TB6600
    digitalWrite(roda.pinoStep, LOW);

    roda.ultimoPassoUs = agora;
    roda.passosAcumulados += roda.sentidoFrente ? 1 : -1;
    if (roda.passosRestantes > 0) {
      roda.passosRestantes--;
    }
  }
};

}  // namespace orion
