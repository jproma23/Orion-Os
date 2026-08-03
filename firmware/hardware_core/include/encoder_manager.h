// Encoder Manager (Cap 10 secao 10) - conta as voltas da roda de verdade.
//
// MONTAGEM (definida pelo dono em 2026-08-02): um SENSOR INDUTIVO NPN por
// roda, com SEIS CHAPAS metalicas distribuidas nela. Cada chapa que passa
// pelo sensor gera um pulso. Ver include/pins.h para a ligacao eletrica e
// para o motivo de NPN ser seguro num pino de 3,3V.
//
// ## Por que isto importa mais que parece
//
// Ate 2026-08-02 a pose do robo vinha dos passos COMANDADOS - o software
// somava o que MANDOU o motor fazer, nunca o que a roda fez. O resultado
// medido: o robo afirmou ter andado 85 cm e girado 80 graus com os motores
// FISICAMENTE DESLIGADOS. Nao era um erro de conta; era uma medida que nunca
// olhou para o mundo. Este arquivo e o fim disso.
//
// ## A CHAPA MAIOR e uma marca de volta, nao um detalhe
//
// Ideia do dono: uma das seis chapas e MAIOR que as outras. Com seis chapas
// iguais, todos os pulsos sao indistinguiveis - se um for perdido por ruido,
// nada denuncia, e o erro fica na conta para sempre. A chapa maior fica mais
// tempo na frente do sensor e produz um pulso mais LARGO: uma marca que
// aparece uma vez por volta.
//
// O detalhe que decide o codigo: o que a chapa maior muda e a LARGURA do
// pulso, nao o intervalo entre pulsos. O intervalo depende de onde as chapas
// estao na roda; a largura depende do tamanho delas. Por isso a interrupcao
// aqui e por CHANGE - precisa dos dois flancos, a chegada e a saida da chapa.
// Contar so a chegada (RISING) daria a contagem certa e cegueira total quanto
// a marca.
#pragma once

#include <Arduino.h>

namespace orion {

//: Quantas chapas ha em cada roda. Se o dono mudar a montagem, e AQUI.
constexpr uint8_t CHAPAS_POR_VOLTA = 6;

//: Quanto mais larga a chapa-marca precisa ser para contar como marca.
//:
//: 1,6x e folgado de proposito. A largura de um pulso varia com a VELOCIDADE
//: da roda: acelerando, um pulso comum pode sair mais largo que o anterior
//: sem ser a marca. Exigir 60% a mais que a media recente separa a marca da
//: variacao normal de velocidade. Se a chapa maior for so um pouco maior que
//: as outras, baixar este numero e mais barato que refazer a chapa - mas
//: abaixo de ~1,3x comeca a confundir com aceleracao.
constexpr float FATOR_LARGURA_MARCA = 1.6f;

//: Pulsos mais curtos que isto sao ruido, nao chapa.
//:
//: Motor de passo perto de fio longo injeta transientes de microssegundos. A
//: roda mais rapida deste robo nao chega perto de gerar um pulso legitimo tao
//: curto - a 2 voltas por segundo com 6 chapas, o pulso mais estreito
//: plausivel ainda passa de 10 ms. 800 us descarta o ruido com margem enorme
//: e sem risco de descartar chapa.
constexpr unsigned long LARGURA_MINIMA_PULSO_US = 800;

//: Estado de uma roda, montado FORA da interrupcao.
struct LeituraEncoder {
  //: Pulsos acumulados desde o boot. E a contagem crua, sem correcao.
  unsigned long pulsos = 0;
  //: Voltas completas confirmadas PELA MARCA (nao por pulsos/6).
  unsigned long voltas = 0;
  //: Quantas vezes a marca apareceu com um numero de pulsos diferente de
  //: CHAPAS_POR_VOLTA desde a marca anterior. E o termometro de saude do
  //: sensor: zero significa que nenhum pulso esta sendo perdido nem
  //: inventado. Vai para a telemetria de proposito - sem este numero,
  //: "a odometria esta certa?" volta a ser opiniao.
  unsigned long desalinhamentos = 0;
  //: Pulsos vistos desde a ultima marca. Deve viver entre 0 e 5.
  uint8_t pulsos_na_volta = 0;
  //: Largura do ultimo pulso, em microssegundos. Util na bancada para
  //: escolher FATOR_LARGURA_MARCA olhando numero em vez de chutar.
  unsigned long ultima_largura_us = 0;
  //: Largura da ultima marca reconhecida.
  unsigned long ultima_marca_us = 0;
  //: Falso ate a primeira marca aparecer. Antes disso `voltas` nao vale nada
  //: e quem le precisa saber disso.
  bool marca_vista = false;
};

class EncoderManager {
 public:
  void iniciar();

  //: Chamar todo loop(). Consome o que as interrupcoes deixaram e faz a
  //: analise (marca, desalinhamento) FORA da ISR - ISR curta e regra, nao
  //: estilo: a deste firmware divide o processador com os pulsos de STEP dos
  //: motores, e uma ISR longa vira passo perdido.
  void atualizar();

  const LeituraEncoder& esquerdo() const { return _esquerdo; }
  const LeituraEncoder& direito() const { return _direito; }

  //: Zera a contagem das duas rodas. Existe para o Motion Core comecar um
  //: trecho de movimento do zero, em vez de fazer contas com um acumulado
  //: que ja passou por quem sabe quantas correcoes.
  void zerar();

 private:
  static void IRAM_ATTR _isrEsquerdo();
  static void IRAM_ATTR _isrDireito();

  void _consumir(LeituraEncoder& leitura, volatile unsigned long* larguras,
                 volatile uint8_t& escrita, uint8_t& leitura_idx, unsigned long& media_us);

  LeituraEncoder _esquerdo;
  LeituraEncoder _direito;
  uint8_t _lidoEsquerdo = 0;
  uint8_t _lidoDireito = 0;
  //: Media corrente da largura dos pulsos COMUNS de cada roda, usada como
  //: referencia para reconhecer a marca. Corrente, e nao fixa, porque a
  //: largura muda com a velocidade: uma referencia fixa so funcionaria numa
  //: unica velocidade.
  unsigned long _mediaEsquerdo = 0;
  unsigned long _mediaDireito = 0;
};

}  // namespace orion
