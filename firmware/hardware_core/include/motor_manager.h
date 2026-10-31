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

// CANAIS LEDC DOS MOTORES: 14 e 15, NUNCA 0 e 1 (bug achado em 2026-10-25).
// A ESP32Servo distribui canais a partir do 0: o pan pega o 0 e o tilt o 1.
// Com o motor tambem no 0/1, o attach() dos servos (que roda DEPOIS do
// motores.iniciar()) reconfigurava os canais para 50 Hz - o motor recebia
// pulso de servo e nao girava, e todo ledcWrite do motor mexia no pan. Os
// canais 14/15 sao do grupo low-speed, que a ESP32Servo so alcanca depois
// de ocupar outros 12 canais.
constexpr uint8_t CANAL_LEDC_ESQ = 14;
constexpr uint8_t CANAL_LEDC_DIR = 15;

enum class TipoMovimento : uint8_t { NENHUM, FRENTE, RE, GIRO };

// --- MANTER A RETA: bussola + encoder (2026-10-31) ---
// Pedido do dono: "controle pid orientado pela bussola para corrigir
// problemas de alinhamento na direcao" e "usando o encoder junto". Medido na
// bancada no mesmo dia: com o MESMO PWM, a roda direita da 10-15% mais
// pulsos que a esquerda - no chao o robo puxaria para a esquerda.
//
// E uma malha POR CIMA do PID de velocidade de cada roda: ela nao mexe no
// PWM, mexe no ALVO de velocidade. Desvio para a esquerda (positivo) -> a
// esquerda acelera e a direita freia na mesma medida. A conta vale igual
// para frente e para re.
//
// O DESVIO E UMA FUSAO COMPLEMENTAR dos dois sensores:
//   - o ENCODER ve na hora qualquer diferenca entre as rodas, sem ruido -
//     mas nao ve escorregao e acumula erro com o tempo;
//   - a BUSSOLA da o rumo absoluto - mas e lenta (10 Hz) e o motor ligado
//     perto dela faz ruido.
// Entao o desvio anda junto com o encoder a cada ciclo, e a bussola o puxa
// devagar (constante RETA_TAU_BUSSOLA_S) para o rumo verdadeiro. Sem bussola
// calibrada, fica so o encoder - que existe sempre.
//
// GANHOS SAO PRIMEIRO PALPITE: 10 graus de desvio -> 0,5 rad/s de diferenca
// (com 2 rad/s de base, a correcao fica em 25%). Teto de 40% da base para a
// correcao nunca inverter uma roda. Ajustar com o robo no chao.
constexpr float RETA_KP = 0.05f;            // rad/s por grau
constexpr float RETA_KI = 0.02f;            // rad/s por grau*s
constexpr float RETA_INTEGRAL_MAX = 25.0f;  // grau*s
constexpr float RETA_CORRECAO_MAX = 0.4f;   // fracao da velocidade base
constexpr float RETA_TAU_BUSSOLA_S = 1.0f;  // quao devagar a bussola corrige o encoder
// +1 se o rumo da bussola CRESCE quando o robo gira no sentido horario
// (visto de cima). E o normal com o modulo de face para cima; montado de
// cabeca para baixo, o rumo cresce ao contrario e isto vira -1.
// CONFERIR: girar o robo a mao para a direita e ver `rumo_graus` subir.
constexpr float RUMO_SENTIDO_HORARIO = 1.0f;

struct EstadoRoda {
  uint8_t pinoIn1 = 0;
  uint8_t pinoIn2 = 0;
  uint8_t pinoEn = 0;
  uint8_t canalLedc = 0;
  //: qual IN esta com o PWM agora (0xFF = nenhum). O PWM vai no IN do
  //: sentido em que a roda gira; o outro IN fica em LOW (pins.h, 25/10).
  uint8_t pinoPwmAtivo = 0xFF;
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
                     pinos::MOTOR_ESQ_EN, 0, CANAL_LEDC_ESQ, pinos::ENCODER_ESQ,
                     pinos::DIR_INVERTIDO_ESQUERDO);
    _configurarRoda(_direita, pinos::MOTOR_DIR_IN3, pinos::MOTOR_DIR_IN4,
                     pinos::MOTOR_DIR_EN, 1, CANAL_LEDC_DIR, pinos::ENCODER_DIR,
                     pinos::DIR_INVERTIDO_DIREITO);
  }

  // L298N nao tem pino de habilitacao separado como o TB6600 (a propria
  // velocidade/PWM em zero ja "desabilita") - mantidos so pela API
  // identica que command_executor.h/main.cpp esperam.
  void habilitar() {}
  void desabilitar() { parar(); }

  // O rumo da bussola de agora, para a malha da reta. Chamar em todo loop()
  // ANTES de atualizar(). `valido` = conectada, calibrada e com leitura.
  void informarRumo(bool valido, float graus) {
    _rumoValido = valido;
    _rumoGraus = graus;
  }

  // Chamar em todo loop() - roda o PID das duas rodas com o dt real.
  void atualizar() {
    unsigned long agora = micros();
    if (_ultimaAtualizacaoUs == 0) {
      _ultimaAtualizacaoUs = agora;
      return;
    }
    float dt_s = (agora - _ultimaAtualizacaoUs) / 1000000.0f;
    _ultimaAtualizacaoUs = agora;

    if ((_tipo == TipoMovimento::FRENTE || _tipo == TipoMovimento::RE) &&
        !_esquerda.concluido && !_direita.concluido) {
      _corrigirReta(dt_s);
    }

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
    _iniciarReta();
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
    _iniciarReta();
  }

  void andarDistancia(float distanciaCm, float velocidadePercent) {
    _ligarRele();
    int64_t pulsos = static_cast<int64_t>(fabsf(distanciaCm) * (PULSOS_POR_METRO / 100.0f));
    bool frente = distanciaCm >= 0;
    _tipo = frente ? TipoMovimento::FRENTE : TipoMovimento::RE;
    _iniciarMovimento(_esquerda, frente, velocidadePercent, pulsos);
    _iniciarMovimento(_direita, frente, velocidadePercent, pulsos);
    _iniciarReta();
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

  // BANCADA (TEST_WHEEL, 2026-10-25): uma roda so, potencia fixa, SEM PID -
  // para conferir fiacao de motor e encoder com as rodas no ar. Quem chama e
  // responsavel por parar() depois. sentidoIn1 = PWM no IN1/IN3 (pins.h).
  void testeMalhaAberta(bool esquerda, bool sentidoIn1, float fracao) {
    parar();
    EstadoRoda& roda = esquerda ? _esquerda : _direita;
    fracao = constrain(fracao, 0.0f, 1.0f);
    _aplicarPonte(roda, sentidoIn1, static_cast<uint32_t>(fracao * PWM_DUTY_MAX));
  }

  //: As DUAS rodas em malha aberta, com a MESMA regra de espelhamento do
  //: movimento normal (`_atualizarRoda`: saida > 0 = frente, e o lado com
  //: dirInvertido troca o par de IN). Existe para a bancada provar, antes do
  //: PID, que "frente" no firmware empurra o robo para a frente de verdade
  //: (2026-10-31). `frente`: true = frente do robo, false = re.
  void testeAmbasMalhaAberta(bool frente, float fracao) {
    parar();
    uint32_t duty = static_cast<uint32_t>(constrain(fracao, 0.0f, 1.0f) * PWM_DUTY_MAX);
    for (EstadoRoda* roda : {&_esquerda, &_direita}) {
      bool sentidoIn1 = roda->dirInvertido ? !frente : frente;
      _aplicarPonte(*roda, sentidoIn1, duty);
    }
  }

  int64_t pulsosBrutos(bool esquerda) const {
    return (esquerda ? _esquerda : _direita).encoder.pulsos();
  }

  bool emMovimento() const {
    return !_esquerda.concluido || !_direita.concluido;
  }

  TipoMovimento movimentoAtual() const {
    return emMovimento() ? _tipo : TipoMovimento::NENHUM;
  }

  bool girandoNoLugar() const { return movimentoAtual() == TipoMovimento::GIRO; }

  //: Quem a malha da reta esta seguindo: "bussola+encoder", "encoder" ou
  //: "nenhuma" (fora de uma reta).
  const char* fonteReta() const {
    TipoMovimento tipo = movimentoAtual();
    if (tipo != TipoMovimento::FRENTE && tipo != TipoMovimento::RE) return "nenhuma";
    return _rumoValido ? "bussola+encoder" : "encoder";
  }
  //: Desvio de rumo da reta, em graus (positivo = desviou para a esquerda).
  //: Fica com o valor da ultima reta depois que ela acaba - e isso que a
  //: telemetria quer mostrar.
  float desvioRetaGraus() const { return _retaDesvio; }

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

  bool _rumoValido = false;
  float _rumoGraus = 0.0f;
  float _retaRumoAlvo = 0.0f;
  bool _retaTemRumoAlvo = false;
  float _retaBaseRadS = 0.0f;
  float _retaIntegral = 0.0f;
  float _retaDesvio = 0.0f;
  float _retaDesvioEncoderAnterior = 0.0f;
  int64_t _retaEsqInicio = 0;
  int64_t _retaDirInicio = 0;

  // Guarda o rumo e os encoders de PARTIDA. Chamado depois de
  // _iniciarMovimento, quando o alvo das duas rodas ja e o mesmo.
  void _iniciarReta() {
    _retaTemRumoAlvo = _rumoValido;
    _retaRumoAlvo = _rumoGraus;
    _retaBaseRadS = _esquerda.alvoRadS;
    _retaIntegral = 0.0f;
    _retaDesvio = 0.0f;
    _retaDesvioEncoderAnterior = 0.0f;
    _retaEsqInicio = _esquerda.deslocamentoAcumulado;
    _retaDirInicio = _direita.deslocamentoAcumulado;
  }

  // -180..180: quanto falta girar de `atual` ate `alvo`, pelo lado mais curto.
  static float _diferencaAngular(float alvo, float atual) {
    return fmodf(alvo - atual + 540.0f, 360.0f) - 180.0f;
  }

  void _corrigirReta(float dt_s) {
    // ENCODER: tracao diferencial - o robo girou (dDir - dEsq) / bitola
    // radianos para a ESQUERDA. Em pulsos, cada grau de giro no lugar e
    // PULSOS_POR_GRAU em CADA roda, dai o 2 no denominador.
    float dEsq = static_cast<float>(_esquerda.deslocamentoAcumulado - _retaEsqInicio);
    float dDir = static_cast<float>(_direita.deslocamentoAcumulado - _retaDirInicio);
    float desvioEncoder = (dDir - dEsq) / (2.0f * PULSOS_POR_GRAU);
    _retaDesvio += desvioEncoder - _retaDesvioEncoderAnterior;
    _retaDesvioEncoderAnterior = desvioEncoder;

    // BUSSOLA: so corrige se havia rumo valido na PARTIDA (senao nao ha alvo
    // absoluto) e se ainda ha agora. Rumo caindo para a esquerda = desvio
    // positivo, a mesma convencao do encoder.
    if (_retaTemRumoAlvo && _rumoValido) {
      float desvioBussola = RUMO_SENTIDO_HORARIO * _diferencaAngular(_retaRumoAlvo, _rumoGraus);
      float beta = constrain(dt_s / RETA_TAU_BUSSOLA_S, 0.0f, 1.0f);
      _retaDesvio += beta * (desvioBussola - _retaDesvio);
    }

    _retaIntegral = constrain(_retaIntegral + _retaDesvio * dt_s, -RETA_INTEGRAL_MAX, RETA_INTEGRAL_MAX);
    float limite = RETA_CORRECAO_MAX * fabsf(_retaBaseRadS);
    float correcao = constrain(RETA_KP * _retaDesvio + RETA_KI * _retaIntegral, -limite, limite);
    _esquerda.alvoRadS = _retaBaseRadS + correcao;
    _direita.alvoRadS = _retaBaseRadS - correcao;
  }

  void _ligarRele() { digitalWrite(pinos::RELE_MOTORES, LOW); }
  static void _desligarRele() { digitalWrite(pinos::RELE_MOTORES, HIGH); }

  void _configurarRoda(EstadoRoda& roda, uint8_t in1, uint8_t in2, uint8_t en,
                        int unidadePcnt, int canalLedc, int pinoEncoder,
                        bool dirInvertido) {
    roda.pinoIn1 = in1;
    roda.pinoIn2 = in2;
    roda.pinoEn = en;
    roda.canalLedc = canalLedc;
    roda.unidadePcnt = unidadePcnt;
    roda.pinoEncoder = pinoEncoder;
    roda.dirInvertido = dirInvertido;

    pinMode(in1, OUTPUT);
    pinMode(in2, OUTPUT);
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);

    // ENA/ENB sempre habilitados (pins.h). Quem para a roda e IN1=IN2=LOW.
    pinMode(en, OUTPUT);
    digitalWrite(en, HIGH);

    ledcSetup(canalLedc, PWM_FREQ_HZ, PWM_RESOLUCAO_BITS);
    roda.pinoPwmAtivo = 0xFF;

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
    _soltarPwm(roda);
  }

  // Tira o PWM do IN que estava com ele e deixa os dois IN em LOW (roda
  // solta). pinMode de novo porque, depois do ledcDetachPin, o pino nao
  // volta sozinho a ser GPIO comum.
  static void _soltarPwm(EstadoRoda& roda) {
    ledcWrite(roda.canalLedc, 0);
    if (roda.pinoPwmAtivo != 0xFF) {
      ledcDetachPin(roda.pinoPwmAtivo);
      roda.pinoPwmAtivo = 0xFF;
    }
    pinMode(roda.pinoIn1, OUTPUT);
    pinMode(roda.pinoIn2, OUTPUT);
    digitalWrite(roda.pinoIn1, LOW);
    digitalWrite(roda.pinoIn2, LOW);
  }

  // Liga o PWM no IN do sentido pedido. So mexe no roteamento quando o
  // sentido muda; no resto do tempo e um ledcWrite.
  static void _aplicarPonte(EstadoRoda& roda, bool sentidoIn1, uint32_t duty) {
    uint8_t ativo = sentidoIn1 ? roda.pinoIn1 : roda.pinoIn2;
    if (roda.pinoPwmAtivo != ativo) {
      _soltarPwm(roda);
      ledcAttachPin(ativo, roda.canalLedc);
      roda.pinoPwmAtivo = ativo;
    }
    ledcWrite(roda.canalLedc, duty);
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

    uint32_t duty = static_cast<uint32_t>(fabsf(saidaFisica) * PWM_DUTY_MAX);
    _aplicarPonte(roda, saidaFisica >= 0, duty);
  }
};

}  // namespace orion
