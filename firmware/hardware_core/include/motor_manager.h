// Motor Manager (Cap 10 secao 10) - controle do motor DC JGB37-520 (encoder
// + reducao) via L298N, com PID de velocidade.
//
// SUBSTITUI o controle por steppers TB6600 (2026-09-13, decisao do dono):
// o motor deste robo trocou de stepper pra DC com encoder, e o dono decidiu
// consolidar o motor NESTE MESMO ESP32 que ja tem bussola e pan/tilt, em vez
// de um ESP32 dedicado separado (revisao do EDR-0031 - ver
// docs/edr/EDR-0031-contrato-esp32-motor.md).
//
// A API PUBLICA desta classe e IDENTICA a versao de steppers (mesmos nomes
// de metodo) de proposito: command_executor.h, safety_manager.h,
// telemetry_manager.h e main.cpp continuam chamando exatamente o que
// chamavam antes - so o que acontece POR DENTRO mudou.
//
// ENCODER DE CANAL UNICO (nao quadratura completa) - e O MOTIVO ORIGINAL JA
// NAO VALE. A razao era que o segundo canal por roda esbarraria nos ECHO dos
// ultrassons, e o frontal (GPIO34) alimentava o SafetyManager: nao se
// arrisca seguranca por resolucao de encoder.
//
// Os ultrassons SAIRAM do robo em 2026-09-19 (quem mede distancia agora e o
// LiDAR, no Raspberry). GPIO 34 e 35 estao livres, sao input-only, e o
// encoder que chegou TEM canal B (fio verde - confirmado na ficha do
// fabricante, ver ENCODER_PPR_MOTOR). Ou seja: a quadratura completa deixou
// de ser impossivel e passou a ser so uma tarefa nao feita.
//
// Enquanto ela nao vem, o sentido de giro continua vindo do que foi
// COMANDADO (nao lido do encoder) - ver encoder_pcnt.h. Isso e suficiente
// para o PID, que so precisa do MODULO da velocidade; e insuficiente para
// detectar que a roda girou para o lado errado (ou que foi empurrada).
//
// NAO TESTADO EM HARDWARE REAL: o PWM e o PID nunca rodaram com o motor de
// verdade. O PPR (11) foi confirmado pela ficha em 2026-09-20, mas a
// REDUCAO (1:90) continua sendo suposicao - e ela multiplica tudo: pulso por
// metro, pulso por grau e a velocidade maxima. Rodas no ar no primeiro
// acionamento, sem excecao (regra do projeto).
#pragma once

#include <Arduino.h>
#include <math.h>

#include "encoder_pcnt.h"
#include "pins.h"
#include "velocity_pid.h"

namespace orion {

// --- Geometria e mecanica desta montagem ---
constexpr float RODA_DIAMETRO_M = 0.200f;  // roda de 200 mm (mesma medida de antes)

// BITOLA (distancia centro a centro das rodas) NUNCA foi medida nesta
// montagem - o mesmo problema que ja existia com PASSOS_POR_GRAU no codigo
// de steppers. 0,45 m e um palpite plausivel pra um robo pequeno; MEDIR e
// corrigir antes de confiar em qualquer TURN_* com angulo.
constexpr float BITOLA_M = 0.45f;  // SUSPEITO - nunca medido, ver acima

// Reducao do JGB37-520: DECISAO PROVISORIA 1:90 (peca ainda a caminho,
// RETOMADA_MIGRACAO.md 2026-09-13).
constexpr float REDUCAO_ENGRENAGEM = 90.0f;
// PPR do encoder no eixo do MOTOR (antes da reducao): CONFIRMADO em
// 2026-09-20 pelo manual do fabricante que veio com a peca - deixou de ser
// chute. O texto da ficha, literal:
//
//     "Yellow: 11 pulses per circle of signal line motor"
//     "Green:  signal line resolution 11 * deceleration ratio = resolution"
//
// Ou seja: 11 por volta DO MOTOR, e a resolucao na roda e 11 x a reducao -
// exatamente a conta que PULSOS_POR_VOLTA_RODA faz abaixo.
//
// A mesma ficha confirma que o encoder e de QUADRATURA (canal A no fio
// amarelo, canal B no verde). Aqui so o canal A e lido - ver o cabecalho
// sobre canal unico. O canal B continua disponivel no conector, e os GPIO
// 34/35 ficaram livres com a saida dos ultrassons.
constexpr float ENCODER_PPR_MOTOR = 11.0f;
// Canal unico conta as DUAS bordas do pulso (2x, ver encoder_pcnt.cpp) -
// metade da resolucao de uma quadratura 4x completa, mas ainda proporcional
// e suficiente pra realimentar o PID.
constexpr float PULSOS_POR_VOLTA_RODA = ENCODER_PPR_MOTOR * 2.0f * REDUCAO_ENGRENAGEM;
constexpr float PULSOS_POR_METRO = PULSOS_POR_VOLTA_RODA / (PI * RODA_DIAMETRO_M);
// Girando sobre o proprio eixo (tracao diferencial): cada roda percorre um
// circulo de raio = bitola/2. O PI cancela dos dois lados.
constexpr float PULSOS_POR_GRAU = PULSOS_POR_VOLTA_RODA * BITOLA_M / (360.0f * RODA_DIAMETRO_M);

// Velocidade angular MAXIMA da RODA (nao do motor antes da reducao), em
// rad/s. PLACEHOLDER - sem bancada ainda. A ficha do JGB37-520 na reducao
// 1:90 da algo perto de 35-55 rpm sem carga (~3,7-5,8 rad/s); usamos um
// valor conservador ate medir de verdade.
constexpr float VELOCIDADE_MAXIMA_RODA_RAD_S = 4.0f;

// --- Ponte H L298N (EDR-0031: driver que o dono ja tinha) ---
constexpr uint32_t PWM_FREQ_HZ = 20000;      // acima do audivel
constexpr uint8_t PWM_RESOLUCAO_BITS = 10;   // duty 0-1023
constexpr uint32_t PWM_DUTY_MAX = (1u << PWM_RESOLUCAO_BITS) - 1;

enum class TipoMovimento : uint8_t { NENHUM, FRENTE, RE, GIRO };

struct EstadoRoda {
  uint8_t pinoIn1 = 0;
  uint8_t pinoIn2 = 0;
  uint8_t pinoPwm = 0;
  uint8_t canalLedc = 0;
  int unidadePcnt = 0;
  int pinoEncoder = 0;
  //: este lado esta montado espelhado (pins.h) - inverte so a saida da
  //: ponte H. O encoder de canal unico nao precisa de correcao propria: o
  //: sinal do deslocamento vem do que foi COMANDADO, nao do hardware.
  bool dirInvertido = false;

  EncoderPcnt encoder;
  VelocidadePid pid;

  float alvoRadS = 0.0f;
  //: -1 = movimento continuo (ate parar() ser chamado). >=0 = quantidade de
  //: pulsos a percorrer antes de zerar alvoRadS sozinho.
  int64_t pulsosAlvo = -1;
  int64_t pulsosInicioMovimento = 0;
  int64_t pulsosAnterior = 0;
  //: sinal do movimento em curso (+1 frente, -1 re/giro-para-tras) - usado
  //: pra dar sinal a leitura do encoder de canal unico.
  int8_t sinalMovimento = 1;
  //: odometria SINALIZADA, equivalente ao passosAcumulados de antes -
  //: soma delta_pulsos*sinalMovimento a cada ciclo, persiste entre
  //: movimentos.
  int64_t deslocamentoAcumulado = 0;
  bool concluido = true;  // true quando nao ha movimento com alvo pendente
};

class MotorManager {
 public:
  void iniciar() {
    // Rele dos motores (pins.h): mesmo fail-safe de antes - escreve o nivel
    // seguro ANTES do pinMode(OUTPUT).
    digitalWrite(pinos::RELE_MOTORES, HIGH);
    pinMode(pinos::RELE_MOTORES, OUTPUT);
    digitalWrite(pinos::RELE_MOTORES, HIGH);

    _configurarRoda(_esquerda, pinos::MOTOR_ESQ_IN1, pinos::MOTOR_ESQ_IN2,
                     pinos::MOTOR_ESQ_PWM, 0, 0, pinos::ENCODER_ESQ,
                     pinos::DIR_INVERTIDO_ESQUERDO);
    _configurarRoda(_direita, pinos::MOTOR_DIR_IN3, pinos::MOTOR_DIR_IN4,
                     pinos::MOTOR_DIR_PWM, 1, 1, pinos::ENCODER_DIR,
                     pinos::DIR_INVERTIDO_DIREITO);
  }

  // L298N nao tem pino de habilitacao separado como o TB6600 (a propria
  // velocidade/PWM em zero ja "desabilita") - mantidos so pela API
  // identica que command_executor.h/main.cpp esperam.
  void habilitar() {}
  void desabilitar() { parar(); }

  // Chamar em todo loop() - roda o PID das duas rodas com o dt real.
  void atualizar() {
    unsigned long agora = micros();
    if (_ultimaAtualizacaoUs == 0) {
      _ultimaAtualizacaoUs = agora;
      return;
    }
    float dt_s = (agora - _ultimaAtualizacaoUs) / 1000000.0f;
    _ultimaAtualizacaoUs = agora;

    _atualizarRoda(_esquerda, dt_s);
    _atualizarRoda(_direita, dt_s);

    if (_tipo != TipoMovimento::NENHUM && _esquerda.concluido && _direita.concluido) {
      _tipo = TipoMovimento::NENHUM;
      _desligarRele();
    }
  }

  void andarFrente(float velocidadePercent) {
    _ligarRele();
    _tipo = TipoMovimento::FRENTE;
    _iniciarMovimento(_esquerda, true, velocidadePercent, -1);
    _iniciarMovimento(_direita, true, velocidadePercent, -1);
  }

  // RE CONTINUA. Faltava: `andarFrente` ignora o SINAL da velocidade (ela
  // so diz o quanto, nunca o lado), e `andarDistancia` negativa e finita.
  // Sem isto, quem quisesse andar de re sem alvo nao tinha o que chamar -
  // descoberto escrevendo o firmware do corpo em 2026-09-20.
  //
  // Andar de re continua sendo o movimento MENOS protegido do robo: nao ha
  // sensor nenhum apontado para tras.
  void andarRe(float velocidadePercent) {
    _ligarRele();
    _tipo = TipoMovimento::RE;
    _iniciarMovimento(_esquerda, false, velocidadePercent, -1);
    _iniciarMovimento(_direita, false, velocidadePercent, -1);
  }

  void andarDistancia(float distanciaCm, float velocidadePercent) {
    _ligarRele();
    int64_t pulsos = static_cast<int64_t>(fabsf(distanciaCm) * (PULSOS_POR_METRO / 100.0f));
    bool frente = distanciaCm >= 0;
    _tipo = frente ? TipoMovimento::FRENTE : TipoMovimento::RE;
    _iniciarMovimento(_esquerda, frente, velocidadePercent, pulsos);
    _iniciarMovimento(_direita, frente, velocidadePercent, pulsos);
  }

  void girarContinuo(bool sentidoHorario, float velocidadePercent) {
    _ligarRele();
    _tipo = TipoMovimento::GIRO;
    _iniciarMovimento(_esquerda, sentidoHorario, velocidadePercent, -1);
    _iniciarMovimento(_direita, !sentidoHorario, velocidadePercent, -1);
  }

  void girarGraus(float graus, bool sentidoHorario, float velocidadePercent) {
    _ligarRele();
    int64_t pulsos = static_cast<int64_t>(fabsf(graus) * PULSOS_POR_GRAU);
    _tipo = TipoMovimento::GIRO;
    _iniciarMovimento(_esquerda, sentidoHorario, velocidadePercent, pulsos);
    _iniciarMovimento(_direita, !sentidoHorario, velocidadePercent, pulsos);
  }

  void parar() {
    _pararRoda(_esquerda);
    _pararRoda(_direita);
    _tipo = TipoMovimento::NENHUM;
    _desligarRele();
  }

  bool emMovimento() const {
    return !_esquerda.concluido || !_direita.concluido;
  }

  TipoMovimento movimentoAtual() const {
    return emMovimento() ? _tipo : TipoMovimento::NENHUM;
  }

  bool girandoNoLugar() const { return movimentoAtual() == TipoMovimento::GIRO; }

  long passosAcumuladosEsquerda() const {
    return static_cast<long>(_esquerda.deslocamentoAcumulado);
  }
  long passosAcumuladosDireita() const {
    return static_cast<long>(_direita.deslocamentoAcumulado);
  }

 private:
  EstadoRoda _esquerda;
  EstadoRoda _direita;
  TipoMovimento _tipo = TipoMovimento::NENHUM;
  unsigned long _ultimaAtualizacaoUs = 0;

  void _ligarRele() { digitalWrite(pinos::RELE_MOTORES, LOW); }
  static void _desligarRele() { digitalWrite(pinos::RELE_MOTORES, HIGH); }

  void _configurarRoda(EstadoRoda& roda, uint8_t in1, uint8_t in2, uint8_t pwm,
                        int unidadePcnt, int canalLedc, int pinoEncoder,
                        bool dirInvertido) {
    roda.pinoIn1 = in1;
    roda.pinoIn2 = in2;
    roda.pinoPwm = pwm;
    roda.canalLedc = canalLedc;
    roda.unidadePcnt = unidadePcnt;
    roda.pinoEncoder = pinoEncoder;
    roda.dirInvertido = dirInvertido;

    pinMode(in1, OUTPUT);
    pinMode(in2, OUTPUT);
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);

    ledcSetup(canalLedc, PWM_FREQ_HZ, PWM_RESOLUCAO_BITS);
    ledcAttachPin(pwm, canalLedc);
    ledcWrite(canalLedc, 0);

    roda.encoder.iniciar(unidadePcnt, pinoEncoder);
  }

  void _iniciarMovimento(EstadoRoda& roda, bool frente, float velocidadePercent, int64_t pulsos) {
    velocidadePercent = constrain(velocidadePercent, 1.0f, 100.0f);
    float alvo = (velocidadePercent / 100.0f) * VELOCIDADE_MAXIMA_RODA_RAD_S;
    roda.alvoRadS = frente ? alvo : -alvo;
    roda.sinalMovimento = frente ? 1 : -1;
    roda.pulsosAlvo = pulsos;
    roda.pulsosInicioMovimento = roda.encoder.pulsos();
    roda.pulsosAnterior = roda.pulsosInicioMovimento;
    roda.concluido = false;
    roda.pid.zerar();
  }

  void _pararRoda(EstadoRoda& roda) {
    roda.alvoRadS = 0.0f;
    roda.pulsosAlvo = -1;
    roda.concluido = true;
    roda.pid.zerar();
    digitalWrite(roda.pinoIn1, LOW);
    digitalWrite(roda.pinoIn2, LOW);
    ledcWrite(roda.canalLedc, 0);
  }

  void _atualizarRoda(EstadoRoda& roda, float dt_s) {
    if (roda.concluido || dt_s <= 0.0f) return;

    int64_t pulsosAtuais = roda.encoder.pulsos();
    int64_t deltaPulsos = pulsosAtuais - roda.pulsosAnterior;  // sempre >=0
    roda.pulsosAnterior = pulsosAtuais;
    roda.deslocamentoAcumulado += static_cast<int64_t>(roda.sinalMovimento) * deltaPulsos;

    // Movimento com alvo finito (MOVE_DISTANCE / TURN_* com angulo): para
    // sozinho quando percorre os pulsos pedidos - equivalente ao
    // passosRestantes==0 da versao de steppers. Magnitude pura: canal
    // unico nao decrementa, entao a diferenca bruta ja e a distancia
    // percorrida.
    if (roda.pulsosAlvo >= 0) {
      int64_t percorridos = pulsosAtuais - roda.pulsosInicioMovimento;
      if (percorridos >= roda.pulsosAlvo) {
        _pararRoda(roda);
        return;
      }
    }

    float medidoRadS = static_cast<float>(roda.sinalMovimento) *
                        (deltaPulsos / dt_s) * (2.0f * PI / PULSOS_POR_VOLTA_RODA);

    float saida = roda.pid.atualizar(roda.alvoRadS, medidoRadS, dt_s);
    float saidaFisica = roda.dirInvertido ? -saida : saida;

    if (saidaFisica >= 0) {
      digitalWrite(roda.pinoIn1, HIGH);
      digitalWrite(roda.pinoIn2, LOW);
    } else {
      digitalWrite(roda.pinoIn1, LOW);
      digitalWrite(roda.pinoIn2, HIGH);
    }
    uint32_t duty = static_cast<uint32_t>(fabsf(saidaFisica) * PWM_DUTY_MAX);
    ledcWrite(roda.canalLedc, duty);
  }
};

}  // namespace orion
