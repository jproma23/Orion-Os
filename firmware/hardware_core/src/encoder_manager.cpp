#include "encoder_manager.h"

#include "pins.h"

namespace orion {

namespace {

//: Fila circular entre a ISR e o loop(), uma por roda.
//:
//: A ISR SO empurra a largura do pulso aqui e sai. Toda a analise - achar a
//: marca, contar volta, detectar desalinhamento - roda no loop(). Nao e
//: preciosismo: as ISRs deste firmware disputam o processador com a geracao
//: dos pulsos de STEP dos motores, e cada microssegundo gasto dentro de uma
//: interrupcao vira jitter de passo.
//:
//: 16 posicoes com o loop rodando em dezenas de microssegundos e folga
//: enorme: a roda teria que dar mais de 100 voltas por segundo para encher.
constexpr uint8_t CAPACIDADE_FILA = 16;

volatile unsigned long g_largurasEsq[CAPACIDADE_FILA];
volatile unsigned long g_largurasDir[CAPACIDADE_FILA];
volatile uint8_t g_escritaEsq = 0;
volatile uint8_t g_escritaDir = 0;

//: Instante em que a chapa CHEGOU (o sensor NPN puxa para o GND, entao a
//: chegada da chapa e a borda de DESCIDA no pino com pull-up).
volatile unsigned long g_inicioEsq = 0;
volatile unsigned long g_inicioDir = 0;

//: Interrupcao por CHANGE avisa nos dois flancos e nao diz qual foi - por
//: isso a ISR le o pino. Ler um GPIO custa nanossegundos; guardar estado numa
//: variavel e confiar nela custa um bug quando um flanco se perde.
inline void IRAM_ATTR _tratarBorda(uint8_t pino, volatile unsigned long& inicio,
                                   volatile unsigned long* larguras,
                                   volatile uint8_t& escrita) {
  const unsigned long agora = micros();
  if (digitalRead(pino) == LOW) {
    // Chapa CHEGOU (NPN puxou para o GND). So marca a hora.
    inicio = agora;
    return;
  }
  // Chapa SAIU: o pull-up devolveu o pino ao nivel alto. Agora da para medir.
  if (inicio == 0) return;  // subida sem descida correspondente: descarta
  const unsigned long largura = agora - inicio;  // seguro no overflow
  inicio = 0;
  if (largura < LARGURA_MINIMA_PULSO_US) return;  // transiente, nao chapa
  larguras[escrita % CAPACIDADE_FILA] = largura;
  escrita++;
}

}  // namespace

void IRAM_ATTR EncoderManager::_isrEsquerdo() {
  _tratarBorda(pinos::ENCODER_ESQUERDO, g_inicioEsq, g_largurasEsq, g_escritaEsq);
}

void IRAM_ATTR EncoderManager::_isrDireito() {
  _tratarBorda(pinos::ENCODER_DIREITO, g_inicioDir, g_largurasDir, g_escritaDir);
}

void EncoderManager::iniciar() {
  // INPUT_PULLUP e o que faz o sensor NPN funcionar (ver pins.h): a saida
  // dele so sabe ligar no GND; quem define o nivel alto e este resistor.
  // Sem ele o pino flutua e conta pulso que nao existe - odometria de ruido,
  // que parece medida.
  pinMode(pinos::ENCODER_ESQUERDO, INPUT_PULLUP);
  pinMode(pinos::ENCODER_DIREITO, INPUT_PULLUP);
  // CHANGE, e nao RISING: a chapa maior se distingue pela LARGURA do pulso, e
  // largura exige os dois flancos. Com RISING a contagem sairia certa e a
  // marca de volta seria invisivel.
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_ESQUERDO), _isrEsquerdo, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pinos::ENCODER_DIREITO), _isrDireito, CHANGE);
}

void EncoderManager::_consumir(LeituraEncoder& leitura, volatile unsigned long* larguras,
                               volatile uint8_t& escrita, uint8_t& leitura_idx,
                               unsigned long& media_us) {
  while (leitura_idx != escrita) {
    const unsigned long largura = larguras[leitura_idx % CAPACIDADE_FILA];
    leitura_idx++;

    leitura.pulsos++;
    leitura.ultima_largura_us = largura;

    // A marca so pode ser reconhecida quando ja existe referencia de com que
    // largura os pulsos COMUNS estao saindo nesta velocidade. Antes disso
    // tudo e "pulso comum" e a media se forma.
    const bool eh_marca = media_us > 0 && largura > (media_us * FATOR_LARGURA_MARCA);

    if (eh_marca) {
      leitura.ultima_marca_us = largura;
      if (leitura.marca_vista) {
        // Entre duas marcas tem que haver exatamente CHAPAS_POR_VOLTA pulsos
        // contando a propria marca. Divergiu: ou um pulso se perdeu (ruido
        // eletrico, chapa suja) ou entrou um a mais. Nao da para saber qual,
        // e por isso o codigo NAO tenta consertar a contagem - so registra
        // que aconteceu. Contagem corrigida por adivinhacao seria de novo um
        // numero que parece medida.
        if (leitura.pulsos_na_volta + 1 != CHAPAS_POR_VOLTA) {
          leitura.desalinhamentos++;
        }
        leitura.voltas++;
      }
      leitura.marca_vista = true;
      leitura.pulsos_na_volta = 0;
      // A marca NAO entra na media: ela e larga por construcao e puxaria a
      // referencia para cima, ate o ponto em que ela deixaria de se
      // distinguir das outras - a marca desapareceria sozinha depois de
      // algumas voltas.
      continue;
    }

    if (leitura.pulsos_na_volta < 255) leitura.pulsos_na_volta++;

    // Media movel barata (7/8 do valor antigo + 1/8 do novo). Segue a
    // mudanca de velocidade sem precisar guardar historico, e sem divisao
    // cara dentro do laco.
    media_us = (media_us == 0) ? largura : ((media_us * 7 + largura) / 8);
  }
}

void EncoderManager::atualizar() {
  _consumir(_esquerdo, g_largurasEsq, g_escritaEsq, _lidoEsquerdo, _mediaEsquerdo);
  _consumir(_direito, g_largurasDir, g_escritaDir, _lidoDireito, _mediaDireito);
}

void EncoderManager::zerar() {
  noInterrupts();
  _lidoEsquerdo = g_escritaEsq;
  _lidoDireito = g_escritaDir;
  interrupts();
  // A media de largura NAO e zerada: ela descreve a velocidade atual da roda,
  // que nao muda so porque alguem quis recomecar a contagem. Zerar faria a
  // proxima marca passar despercebida ate a referencia se formar de novo.
  const unsigned long mediaEsq = _mediaEsquerdo;
  const unsigned long mediaDir = _mediaDireito;
  _esquerdo = LeituraEncoder{};
  _direito = LeituraEncoder{};
  _mediaEsquerdo = mediaEsq;
  _mediaDireito = mediaDir;
}

}  // namespace orion
