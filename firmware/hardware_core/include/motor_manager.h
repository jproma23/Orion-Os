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
// LiDAR, no Raspberry). GPIO 34 e 35 viraram o encoder direito (A e B,
// pins.h - era "estao livres" ate 2026-11-10), e o encoder que chegou TEM
// canal B (fio verde - confirmado na ficha do
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
// MEDIDO pelo dono em 2026-10-31: 60 mm. O 200 mm era da montagem antiga e
// fazia toda distancia sair 3,3x MENOR que a pedida ("50 cm" andava 15 cm).
// TEM que bater com chassi.roda_diametro_m do orion.yaml (teste preso).
constexpr float RODA_DIAMETRO_M = 0.060f;

// BITOLA (distancia centro a centro das rodas) NUNCA foi medida nesta
// montagem - o mesmo problema que ja existia com PASSOS_POR_GRAU no codigo
// de steppers. 0,45 m e um palpite plausivel pra um robo pequeno; MEDIR e
// corrigir antes de confiar em qualquer TURN_* com angulo.
// BITOLA EFETIVA, MEDIDA GIRANDO (2026-10-31): a fita deu 36 cm de eixo a
// eixo, mas com 0,36 o robo girava ~1,35x o pedido - pedido 90, bussola
// calibrada mediu +121 e -125; oito passos de 45 somaram 488 graus. Com as
// rodas fazendo a velocidade pedida (bancada: 0,070 para 0,072 m/s), quem
// erra e a bitola: 0,36 / 1,35 = 0,27. O que vale para a conta e onde o pneu
// toca o chao, nao a fita. Foi esse erro que fez a "meia volta" do video ser
// quase uma volta inteira - e o robo seguir para a parede.
// TEM que bater com motion.wheel_base_m do orion.yaml (teste preso).
constexpr float BITOLA_M = 0.27f;

// Reducao do JGB37-520 (etiqueta "DC 6V 35RPM"): 1:168, MEDIDA na bancada em
// 2026-10-31 com o dono contando voltas da roda: 9900 pulsos deram ~2,8
// voltas e 35360 deram 9,5 (previsto pelo 1:168: 2,68 e 9,57). O 1:90 era
// palpite de setembro e fazia toda distancia sair 1,87x MAIOR que a pedida.
// TEM que bater com motion.steps_per_meter do orion.yaml (teste preso).
constexpr float REDUCAO_ENGRENAGEM = 168.0f;
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
// sobre canal unico. O canal B ja esta ligado (pins.h: ENCODER_ESQ_B=27,
// ENCODER_DIR_B=35), so nao e lido.
constexpr float ENCODER_PPR_MOTOR = 11.0f;
// Canal unico conta as DUAS bordas do pulso (2x, ver encoder_pcnt.cpp) -
// metade da resolucao de uma quadratura 4x completa, mas ainda proporcional
// e suficiente pra realimentar o PID.
constexpr float PULSOS_POR_VOLTA_RODA = ENCODER_PPR_MOTOR * 2.0f * REDUCAO_ENGRENAGEM;
constexpr float PULSOS_POR_METRO = PULSOS_POR_VOLTA_RODA / (PI * RODA_DIAMETRO_M);
// Girando sobre o proprio eixo (tracao diferencial): cada roda percorre um
// circulo de raio = bitola/2. O PI cancela dos dois lados.
constexpr float PULSOS_POR_GRAU = PULSOS_POR_VOLTA_RODA * BITOLA_M / (360.0f * RODA_DIAMETRO_M);

// Velocidade angular MAXIMA da RODA, em rad/s: a NOMINAL da etiqueta, 35 rpm
// a 6 V (35 x 2pi / 60 = 3,66). E de proposito a de 6 V: a bateria e de 10 V,
// e com 100% = rotacao nominal o PID nunca pede ao motor mais do que ele gira
// na tensao dele. Isso limita a VELOCIDADE pedida; quem limita a TENSAO
// media no motor e o DUTY_MAX_FRACAO logo abaixo.
constexpr float VELOCIDADE_MAXIMA_RODA_RAD_S = 3.66f;

// --- Ponte H L298N (EDR-0031: driver que o dono ja tinha) ---
constexpr uint32_t PWM_FREQ_HZ = 20000;      // acima do audivel
constexpr uint8_t PWM_RESOLUCAO_BITS = 10;   // duty 0-1023
constexpr uint32_t PWM_DUTY_MAX = (1u << PWM_RESOLUCAO_BITS) - 1;

// TETO DE DUTY - LIMITE DE SEGURANCA, NAO DE VELOCIDADE (2026-11-10, C4-02).
//
// >>> ESTE E O LUGAR DO TETO. Mexer aqui muda o PID, o anti-windup e os
// >>> testes de bancada (TEST_WHEEL) de uma vez so.
//
// Por que existe: a bateria e de 10 V e o motor e de 6 V. Sem teto, o PID
// saturava em 100% de duty (~10 V medios no motor) toda vez que a roda nao
// alcancava o alvo - em toda partida, e principalmente com a RODA TRAVADA,
// que fica 1,5 s (TEMPO_RODA_TRAVADA_MS) a 100% antes de a protecao cortar.
// 0,75 x 10 V = 7,5 V: ainda acima do nominal, mas ~25% menos corrente de
// estol no motor e no L298N nesse 1,5 s.
//
// CONSEQUENCIA ACEITA E REGISTRADA: a simulacao de malha fechada da
// auditoria (provas/v_c402_regime.py) mostrou o regime em 60-91% de duty
// para CMD_VEL 0,11 m/s COM CARGA - 0,11 m/s e a velocidade MAXIMA da roda
// (3,66 rad/s x 0,03 m). Com o teto em 75%, com carga pesada ou bateria
// fraca o robo NAO chega a 0,11 m/s: a roda satura no teto, a malha da reta
// nivela pela mais fraca (RETA_TAU_NIVELAR_S) e ele anda mais devagar que o
// pedido, em vez de forcar o motor alem do que ele aguenta. Os 30-40% da voz
// e do EXPLORE ficam longe do teto. Se no chao o robo nao alcancar o que
// precisa, SUBIR ESTE NUMERO e decisao consciente - nao tirar o teto.
constexpr float DUTY_MAX_FRACAO = 0.75f;

// CANAIS LEDC DOS MOTORES: 14 e 15, NUNCA 0 e 1 (bug achado em 2026-10-25).
// A ESP32Servo distribui canais a partir do 0: o pan pega o 0 e o tilt o 1.
// Com o motor tambem no 0/1, o attach() dos servos (que roda DEPOIS do
// motores.iniciar()) reconfigurava os canais para 50 Hz - o motor recebia
// pulso de servo e nao girava, e todo ledcWrite do motor mexia no pan. Os
// canais 14/15 sao do grupo low-speed, que a ESP32Servo so alcanca depois
// de ocupar outros 12 canais.
constexpr uint8_t CANAL_LEDC_ESQ = 14;
constexpr uint8_t CANAL_LEDC_DIR = 15;

// VELOCIDADE (2026-10-31): v e w continuos, vindos do Nav2 (CMD_VEL). Quem
// controla o rumo ai e o Nav2 - a malha da reta NAO roda nesse tipo.
enum class TipoMovimento : uint8_t { NENHUM, FRENTE, RE, GIRO, VELOCIDADE };

// CMD_VEL tem watchdog proprio e CURTO: o Nav2 manda a 10-20 Hz, e se ele
// calar (container caiu, ponte travou, Wi-Fi piscou) o robo para em meio
// segundo - nao nos 5 s do homem-morto geral.
constexpr unsigned long CMD_VEL_TIMEOUT_MS = 500;
// Abaixo disto a roda nem vence o atrito: nao conta como "mandada girar" para
// a deteccao de roda travada (senao um w pequeno do Nav2 a dispararia).
constexpr float CMD_VEL_MINIMO_TRAVA_RAD_S = 0.3f;

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
// GANHOS (bancada 2026-10-31, rodas no ar): com 0,05/0,02 a 40% o desvio
// ficava em +-1 grau, mas a 60% as DUAS rodas saturavam (bateria fraca: a
// esquerda nao passava de 1,56 rad/s, a direita de 1,72) e o desvio subia
// devagar ate 7 graus - a correcao era fraca demais para frear a roda forte
// o bastante. 0,12/0,04 vence essa diferenca com ~3 graus. Teto de 40% da
// base para a correcao nunca inverter uma roda. Reajustar no chao.
constexpr float RETA_KP = 0.12f;            // rad/s por grau
constexpr float RETA_KI = 0.04f;            // rad/s por grau*s
constexpr float RETA_INTEGRAL_MAX = 25.0f;  // grau*s
constexpr float RETA_CORRECAO_MAX = 0.4f;   // fracao da velocidade base
constexpr float RETA_TAU_BUSSOLA_S = 1.0f;  // quao devagar a bussola corrige o encoder
// NIVELAR PELA RODA MAIS FRACA (2026-10-31): se a roda que precisa acelerar
// ja esta no teto do PWM e nao alcanca, nenhuma correcao a faz ir mais rapido
// - o alvo das DUAS desce ate o que ela consegue. Medido: a 60% com bateria
// fraca a esquerda parava em 1,56 rad/s e o desvio ia a 7 graus.
constexpr float RETA_TAU_NIVELAR_S = 0.5f;   // quao rapido o alvo desce
constexpr float RETA_BASE_MINIMA = 0.3f;     // nunca abaixo de 30% do pedido
// +1 se o rumo da bussola CRESCE quando o robo gira no sentido horario
// (visto de cima). E o normal com o modulo de face para cima; montado de
// cabeca para baixo, o rumo cresce ao contrario e isto vira -1.
// CONFERIR: girar o robo a mao para a direita e ver `rumo_graus` subir.
constexpr float RUMO_SENTIDO_HORARIO = 1.0f;

// RODA TRAVADA (2026-10-31): roda mandada girar que nao conta pulso nenhum por
// este tempo para o movimento inteiro. Com a roda de 60 mm o robo anda ~5
// cm/s, e um movimento com fim (MOVE_DISTANCE, TURN com graus) passou a durar
// mais que os 5 s do homem-morto - entao ele nao e cortado pelo homem-morto
// de comando, e duas protecoes cobrem o lugar: ESTA (motor ligado sem a roda
// andar - travada, sem energia, encoder solto - desliga sozinho) e o
// HEARTBEAT do Pi (safety_manager.h, 2026-11-10): Pi calado no meio do
// movimento com fim = parada, em vez de andar ate o fim sem dono.
constexpr unsigned long TEMPO_RODA_TRAVADA_MS = 1500;

// Periodo da malha de controle (PID de velocidade + reta): 50 Hz.
constexpr unsigned long PERIODO_CONTROLE_US = 20000;

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
  //: Velocidade medida no ultimo ciclo (rad/s, com sinal) e se o PWM estava
  //: no teto - a malha da reta usa os dois para nivelar pela roda mais fraca.
  float medidoRadS = 0.0f;
  bool saturada = false;
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
  //: millis() do ultimo pulso contado (ou do inicio do movimento).
  unsigned long ultimoPulsoMs = 0;
};

class MotorManager {
 public:
  // PRIMEIRA COISA DO setup() (main.cpp, 2026-11-10): ate esta funcao
  // rodar, os IN do L298N estao em alta impedancia com a ponte energizada
  // (nao ha mais rele cortando - pins.h). Quanto antes eles virarem saida em
  // LOW, menor a janela do tranco no boot.
  void iniciar() {
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
    // RITMO FIXO (2026-10-31). Ate aqui o controle rodava a cada volta do
    // loop(), milhares de vezes por segundo: entre duas voltas a roda conta 0
    // ou 1 pulso, e a "velocidade medida" pulava entre zero e valores enormes
    // - o PID trabalhava sobre ruido. A 20 ms a roda conta ~15-20 pulsos a
    // 40%, e a medida fica estavel.
    if (agora - _ultimaAtualizacaoUs < PERIODO_CONTROLE_US) return;
    float dt_s = (agora - _ultimaAtualizacaoUs) / 1000000.0f;
    _ultimaAtualizacaoUs = agora;

    if (_tipo == TipoMovimento::VELOCIDADE && millis() - _ultimoCmdVelMs > CMD_VEL_TIMEOUT_MS) {
      parar();
      return;
    }

    if ((_tipo == TipoMovimento::FRENTE || _tipo == TipoMovimento::RE) &&
        !_esquerda.concluido && !_direita.concluido) {
      _corrigirReta(dt_s);
    }

    _atualizarRoda(_esquerda, dt_s);
    _atualizarRoda(_direita, dt_s);

    if (_tipo != TipoMovimento::NENHUM && _esquerda.concluido && _direita.concluido) {
      _tipo = TipoMovimento::NENHUM;
    }
  }

  void andarFrente(float velocidadePercent) {
    _movimentoFinito = false;
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
    _movimentoFinito = false;
    _tipo = TipoMovimento::RE;
    _iniciarMovimento(_esquerda, false, velocidadePercent, -1);
    _iniciarMovimento(_direita, false, velocidadePercent, -1);
    _iniciarReta();
  }

  void andarDistancia(float distanciaCm, float velocidadePercent) {
    _movimentoFinito = true;
    int64_t pulsos = static_cast<int64_t>(fabsf(distanciaCm) * (PULSOS_POR_METRO / 100.0f));
    bool frente = distanciaCm >= 0;
    _tipo = frente ? TipoMovimento::FRENTE : TipoMovimento::RE;
    _iniciarMovimento(_esquerda, frente, velocidadePercent, pulsos);
    _iniciarMovimento(_direita, frente, velocidadePercent, pulsos);
    _iniciarReta();
  }

  void girarContinuo(bool sentidoHorario, float velocidadePercent) {
    _movimentoFinito = false;
    _tipo = TipoMovimento::GIRO;
    _iniciarMovimento(_esquerda, sentidoHorario, velocidadePercent, -1);
    _iniciarMovimento(_direita, !sentidoHorario, velocidadePercent, -1);
  }

  void girarGraus(float graus, bool sentidoHorario, float velocidadePercent) {
    _movimentoFinito = true;
    int64_t pulsos = static_cast<int64_t>(fabsf(graus) * PULSOS_POR_GRAU);
    _tipo = TipoMovimento::GIRO;
    _iniciarMovimento(_esquerda, sentidoHorario, velocidadePercent, pulsos);
    _iniciarMovimento(_direita, !sentidoHorario, velocidadePercent, pulsos);
  }

  // CMD_VEL: velocidade linear (m/s, + = frente) e angular (rad/s, + =
  // anti-horario, convencao ROS). Cinematica diferencial: cada roda faz
  // v -+ w*bitola/2. Se uma passar do maximo, as DUAS encolhem na mesma
  // proporcao - preserva a CURVA pedida, que e o que o Nav2 planejou.
  // v = w = 0 para. Chamar de novo a cada mensagem: e isso que alimenta o
  // watchdog de CMD_VEL_TIMEOUT_MS.
  void definirVelocidade(float v_m_s, float w_rad_s) {
    _ultimoCmdVelMs = millis();
    float raio = RODA_DIAMETRO_M / 2.0f;
    float esq = (v_m_s - w_rad_s * BITOLA_M / 2.0f) / raio;
    float dir = (v_m_s + w_rad_s * BITOLA_M / 2.0f) / raio;
    float maior = fmaxf(fabsf(esq), fabsf(dir));
    if (maior > VELOCIDADE_MAXIMA_RODA_RAD_S) {
      esq *= VELOCIDADE_MAXIMA_RODA_RAD_S / maior;
      dir *= VELOCIDADE_MAXIMA_RODA_RAD_S / maior;
    }
    if (fabsf(esq) < 1e-3f && fabsf(dir) < 1e-3f) {
      parar();
      return;
    }
    _movimentoFinito = false;
    bool novo = _tipo != TipoMovimento::VELOCIDADE;
    _tipo = TipoMovimento::VELOCIDADE;
    _alvoVelocidade(_esquerda, esq, novo);
    _alvoVelocidade(_direita, dir, novo);
  }

  void parar() {
    _pararRoda(_esquerda);
    _pararRoda(_direita);
    _tipo = TipoMovimento::NENHUM;
  }

  // BANCADA (TEST_WHEEL, 2026-10-25): uma roda so, potencia fixa, SEM PID -
  // para conferir fiacao de motor e encoder com as rodas no ar. Quem chama e
  // responsavel por parar() depois. sentidoIn1 = PWM no IN1/IN3 (pins.h).
  // Malha aberta tambem respeita DUTY_MAX_FRACAO (2026-11-10): roda presa na
  // bancada e o mesmo estol do chao.
  void testeMalhaAberta(bool esquerda, bool sentidoIn1, float fracao) {
    parar();
    EstadoRoda& roda = esquerda ? _esquerda : _direita;
    fracao = constrain(fracao, 0.0f, DUTY_MAX_FRACAO);
    _aplicarPonte(roda, sentidoIn1, static_cast<uint32_t>(fracao * PWM_DUTY_MAX));
  }

  //: As DUAS rodas em malha aberta, com a MESMA regra de espelhamento do
  //: movimento normal (`_atualizarRoda`: saida > 0 = frente, e o lado com
  //: dirInvertido troca o par de IN). Existe para a bancada provar, antes do
  //: PID, que "frente" no firmware empurra o robo para a frente de verdade
  //: (2026-10-31). `frente`: true = frente do robo, false = re.
  void testeAmbasMalhaAberta(bool frente, float fracao) {
    parar();
    uint32_t duty = static_cast<uint32_t>(constrain(fracao, 0.0f, DUTY_MAX_FRACAO) * PWM_DUTY_MAX);
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

  //: O movimento em curso para sozinho (distancia ou angulo pedido)? O
  //: homem-morto de comando nao corta estes - quem os protege e a roda
  //: travada (logo abaixo) e o heartbeat do Pi (safety_manager.h).
  bool movimentoComFim() const { return emMovimento() && _movimentoFinito; }
  //: O ultimo movimento parou porque uma roda nao contou pulso por
  //: TEMPO_RODA_TRAVADA_MS. Fica marcado ate o proximo movimento.
  bool rodaTravada() const { return _rodaTravada; }

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

  bool _movimentoFinito = false;
  unsigned long _ultimoCmdVelMs = 0;
  bool _rodaTravada = false;
  bool _rumoValido = false;
  float _rumoGraus = 0.0f;
  float _retaRumoAlvo = 0.0f;
  bool _retaTemRumoAlvo = false;
  float _retaBaseRadS = 0.0f;
  float _retaBasePedidaRadS = 0.0f;
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
    _retaBasePedidaRadS = _esquerda.alvoRadS;
    _esquerda.saturada = _direita.saturada = false;
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

    // Quem precisa ACELERAR: desvio para a esquerda (positivo) = a esquerda
    // ficou para tras. Se ela esta no teto e abaixo da base, a base desce na
    // direcao da velocidade que ela de fato faz; senao volta ao pedido.
    EstadoRoda& lenta = _retaDesvio >= 0.0f ? _esquerda : _direita;
    float pedida = fabsf(_retaBasePedidaRadS);
    float base = fabsf(_retaBaseRadS);
    float alvoBase = pedida;
    if (lenta.saturada && fabsf(lenta.medidoRadS) < base) {
      alvoBase = fmaxf(fabsf(lenta.medidoRadS), RETA_BASE_MINIMA * pedida);
    }
    base += constrain(dt_s / RETA_TAU_NIVELAR_S, 0.0f, 1.0f) * (alvoBase - base);
    _retaBaseRadS = _retaBasePedidaRadS >= 0.0f ? base : -base;

    _retaIntegral = constrain(_retaIntegral + _retaDesvio * dt_s, -RETA_INTEGRAL_MAX, RETA_INTEGRAL_MAX);
    float limite = RETA_CORRECAO_MAX * fabsf(_retaBaseRadS);
    float correcao = constrain(RETA_KP * _retaDesvio + RETA_KI * _retaIntegral, -limite, limite);
    _esquerda.alvoRadS = _retaBaseRadS + correcao;
    _direita.alvoRadS = _retaBaseRadS - correcao;
  }

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

    // O PID satura NO TETO, nao em 1,0: assim o anti-windup (velocity_pid.cpp)
    // para de acumular integral exatamente onde a saida para de crescer.
    roda.pid.definirSaidaMaxima(DUTY_MAX_FRACAO);

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
    roda.ultimoPulsoMs = millis();
    _rodaTravada = false;
    roda.concluido = false;
    roda.pid.zerar();
  }

  // Alvo de uma roda no modo VELOCIDADE. So zera o PID quando o movimento
  // COMECA ou a roda INVERTE: a 10-20 Hz o alvo muda pouco a cada mensagem,
  // e zerar a integral toda vez faria a roda nunca chegar na velocidade.
  void _alvoVelocidade(EstadoRoda& roda, float alvoRadS, bool novo) {
    int8_t sinal = alvoRadS >= 0.0f ? 1 : -1;
    if (novo || roda.concluido || sinal != roda.sinalMovimento) {
      roda.sinalMovimento = sinal;
      roda.pulsosInicioMovimento = roda.encoder.pulsos();
      roda.pulsosAnterior = roda.pulsosInicioMovimento;
      roda.ultimoPulsoMs = millis();
      roda.pid.zerar();
      _rodaTravada = false;
    }
    roda.alvoRadS = alvoRadS;
    roda.pulsosAlvo = -1;
    roda.concluido = false;
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

    bool mandadaGirar = _tipo != TipoMovimento::VELOCIDADE ||
                        fabsf(roda.alvoRadS) >= CMD_VEL_MINIMO_TRAVA_RAD_S;
    if (deltaPulsos > 0 || !mandadaGirar) {
      roda.ultimoPulsoMs = millis();
    } else if (millis() - roda.ultimoPulsoMs > TEMPO_RODA_TRAVADA_MS) {
      // Para as DUAS: uma roda andando sozinha faz o robo girar sem ninguem
      // ter pedido giro.
      _rodaTravada = true;
      parar();
      return;
    }

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

    // `saida` ja vem limitada a +-DUTY_MAX_FRACAO pelo proprio PID.
    float saida = roda.pid.atualizar(roda.alvoRadS, medidoRadS, dt_s);
    roda.medidoRadS = medidoRadS;
    roda.saturada = fabsf(saida) >= 0.99f * DUTY_MAX_FRACAO;
    float saidaFisica = roda.dirInvertido ? -saida : saida;

    // fminf de novo, de proposito: o teto nao pode depender so do PID estar
    // configurado certo.
    uint32_t duty = static_cast<uint32_t>(fminf(fabsf(saidaFisica), DUTY_MAX_FRACAO) * PWM_DUTY_MAX);
    _aplicarPonte(roda, saidaFisica >= 0, duty);
  }
};

}  // namespace orion
