// Encoder Manager (Cap 10 secao 10).
//
// RESERVADO: pinos 18/19 nao tem encoder fisico ligado nesta montagem
// (confirmado com o usuario) - a contagem fica em zero ate os encoders
// serem instalados, sem crash nem comportamento indefinido. O Motor
// Manager ja fornece uma odometria aproximada por contagem de passos
// enquanto isso.
//
// ## Um encoder so e um caso previsto (2026-08-13)
//
// O dono decidiu comprar UM encoder. O lado sem encoder simplesmente conta
// zero, e quem resolve isso e o Motion Core: `motion.encoder_lado` no
// config diz qual lado tem sensor fisico, e `fusao_sensores.py` recupera a
// distancia do centro pela geometria diferencial usando o rumo. Este
// firmware nao precisa saber de nada disso - so contar honestamente.
//
// ## De onde vem o SINAL da contagem
//
// Um encoder de canal unico (disco furado + LM393, o barato) conta pulso
// mas NAO sabe para que lado a roda girou: o sinal e o mesmo indo e
// voltando. Por isso o sentido vem do que foi COMANDADO ao motor, via
// `definirSentido()`, exatamente como o MotorManager ja faz com os passos
// (`passosAcumulados += sentidoFrente ? 1 : -1`).
//
// A consequencia honesta disso: se a roda girar SEM ter sido comandada -
// robo empurrado, descendo uma rampa por gravidade, motor destravando com
// o robo em cima - a contagem anda para o lado errado. Um encoder de
// QUADRATURA (canais A e B) resolveria de verdade, porque a defasagem
// entre os dois canais da o sentido pelo proprio sensor. Se um dia entrar
// quadratura aqui, `definirSentido()` deixa de ser usado e a ISR passa a
// ler o canal B - o resto (contadores, leitura atomica, telemetria) nao
// muda.
//
// ## AO PORTAR PARA O ESP32 (o corpo real desde 2026-08-02)
//
// Este arquivo foi escrito contra o Mega. Tres coisas mudam, e nenhuma e
// opcional:
//
//   1. PINOS. `ENCODER_ESQUERDO/DIREITO` sao 18/19 porque no Mega so
//      alguns pinos tem interrupcao externa. No ESP32 qualquer GPIO
//      interrompe, entao a escolha passa a ser por fiacao - fugindo dos
//      pinos de boot (0, 2, 12, 15 - o rele ja mordeu o projeto no GPIO2,
//      que impede o boot; ver docs/journal.md) e dos so-entrada (34-39 nao
//      tem pull-up interno, e o INPUT_PULLUP daqui viraria silenciosamente
//      nada).
//   2. ATOMICIDADE. `<util/atomic.h>` e do AVR e nao existe no ESP32; la o
//      equivalente e `portENTER_CRITICAL`/`portEXIT_CRITICAL` com um
//      `portMUX_TYPE`. O motivo de existir continua o mesmo (leitura
//      rasgada de 4 bytes) - o ESP32 e de 32 bits, mas a leitura ainda
//      compete com a ISR e com o outro nucleo.
//   3. ISR NA RAM. No ESP32 a ISR precisa de `IRAM_ATTR`, senao pode ser
//      chamada com a flash ocupada e travar o chip.
//
// Vale considerar trocar a ISR pelo PCNT, o contador de pulso em HARDWARE
// do ESP32: ele conta sem acordar a CPU e ja tem filtro de repique
// embutido, o que tornaria `INTERVALO_MINIMO_PULSO_US` desnecessario.
#pragma once

#include <Arduino.h>

namespace orion {

// Menor intervalo aceito entre dois pulsos do MESMO encoder. Serve contra
// o repique do comparador (LM393 sem histerese suficiente dispara varias
// vezes na mesma borda, e cada repique vira distancia inventada).
//
// Como recalcular para o SEU disco, se trocar:
//   pulsos_por_segundo_max = furos_por_volta * voltas_por_segundo_max
//   intervalo_real_us      = 1e6 / pulsos_por_segundo_max
// e escolher algo bem abaixo do intervalo real. Com disco de 20 furos e
// roda a 3 voltas/s (rapido para este chassi), sao 60 pulsos/s = 16.666 us
// entre pulsos - os 300 us abaixo sao 50x mais rapidos que o pulso mais
// rapido plausivel, entao cortam repique sem nunca cortar pulso legitimo.
//
// Se um dia a contagem ficar MENOR que a real em velocidade alta, este
// numero e o primeiro suspeito: significa que ele passou a cortar pulso
// bom, e nao repique.
constexpr unsigned long INTERVALO_MINIMO_PULSO_US = 300;

class EncoderManager {
 public:
  void iniciar();

  // Sentido COMANDADO de cada roda, alimentado pelo loop a partir do
  // MotorManager. Sem isso a contagem so sabe subir, e toda marcha a re
  // seria somada como avanco.
  void definirSentido(bool frenteEsquerda, bool frenteDireita);

  long pulsosEsquerdo() const;
  long pulsosDireito() const;

  // Zera os dois contadores. O Motion Core trata "os dois zeraram juntos"
  // como reinicio do Hardware Core e ressincroniza sem mexer na pose
  // (fusao_sensores.py), entao chamar isto no meio de uma missao custa um
  // quadro de telemetria, nao a pose inteira.
  void zerar();

 private:
  static void _isrEsquerdo();
  static void _isrDireito();
};

}  // namespace orion
