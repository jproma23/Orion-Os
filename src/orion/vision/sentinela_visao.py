"""Sentinela de visão (Cap 8; EDR-0020 Modo Sentinela): dispara o alerta de
ROSTO DESCONHECIDO que o maestro (Vigília) trata.

Desde 2026-07-25 (EDR-0023) ela NÃO abre mais a câmera nem roda
reconhecimento facial próprio. Quem faz as duas coisas é o Vision Core, dono
do dispositivo; a Sentinela apenas ouve `vision.faces_desconhecidas` no
Event Bus e decide se aquilo vira alerta.

Duas coisas melhoraram com isso:

1. Acabou a disputa pelo `/dev/videoN`. Antes, Vision Core e Sentinela
   abriam a mesma câmera - e era por isso que o Vision Core nunca podia
   entrar em produção.
2. Acabou o reconhecimento facial DUPLICADO. Os dois rodavam
   `face_recognition` sobre os mesmos frames, e é a parte mais cara do
   Notebook (o alívio de carga existe justamente por causa dela).

O cooldown continua aqui, e não no Vision Core: é uma decisão de política de
alerta ("não repetir enquanto o estranho continua no quadro"), não uma
decisão de visão.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from collections.abc import Callable

from orion.kernel.event_bus import EventBus, Prioridade

logger = logging.getLogger("orion.vision.sentinela_visao")

#: recebe o caminho de destino, grava a imagem atual e devolve o caminho (ou
#: None se ainda não houve frame). Quem fornece é o dono da câmera.
SalvarFoto = Callable[[str], str | None]


class SentinelaVisao:
    def __init__(
        self,
        event_bus: EventBus,
        cooldown_s: float,
        pasta_fotos: str,
        salvar_foto: SalvarFoto | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._cooldown_s = cooldown_s
        self._pasta_fotos = Path(pasta_fotos)
        self._salvar_foto = salvar_foto
        self._ultimo_alerta = 0.0
        self._pausado = False

    def registrar(self) -> None:
        """Assina o evento do Vision Core. Substitui o antigo executar()."""
        self._pasta_fotos.mkdir(parents=True, exist_ok=True)
        self._event_bus.subscribe("vision.faces_desconhecidas", self._ao_ver_desconhecido)
        # `sentinela.capturar_foto` nasce no Pi: a Vigília pede foto ao assumir
        # um alerta, e `fotografar` é uma das ações do contrato que a IA
        # estratégica pode escolher. Até 2026-07-29 NINGUÉM assinava este
        # tópico em NENHUMA das duas máquinas - o robô "registrava evidência"
        # e nada era registrado. Mesmo padrão do guardião de RAM e do
        # pan/tilt: publicar evento não falha, então nada denunciava.
        #
        # O consumidor é aqui porque a foto exige o frame, e o frame é do dono
        # da câmera (EDR-0023 s.3) - a Sentinela já tem o `salvar_foto` dele.
        self._event_bus.subscribe("sentinela.capturar_foto", self._ao_pedir_foto)
        logger.info("Sentinela de visão ativa - ouvindo o Vision Core")

    def pausar(self) -> None:
        """Suspende os alertas sem desassinar o evento (ver retomar).

        Usado pelo alívio de carga. Note que agora pausar a Sentinela NÃO
        economiza CPU - quem gasta é o Vision Core, que continua rodando.
        Para aliviar de verdade é o Vision Core que precisa ser pausado.
        """
        if not self._pausado:
            self._pausado = True
            logger.warning("Sentinela de visão PAUSADA (alívio de carga)")

    def retomar(self) -> None:
        if self._pausado:
            self._pausado = False
            logger.info("Sentinela de visão retomada")

    @property
    def pausado(self) -> bool:
        return self._pausado

    async def _ao_ver_desconhecido(self, evento) -> None:
        if self._pausado:
            return

        agora = time.monotonic()
        if agora - self._ultimo_alerta < self._cooldown_s:
            return  # ainda no cooldown - não repete enquanto o estranho fica
        self._ultimo_alerta = agora

        quantidade = evento.dados.get("quantidade", 1)
        caminho = self._pedir_foto()
        logger.warning("SENTINELA: %d rosto(s) desconhecido(s) - alerta!", quantidade)
        await self._event_bus.publish(
            "sentinela.alerta",
            {"tipo": "pessoa", "desconhecidos": quantidade, "foto": caminho},
            prioridade=Prioridade.ALTA,
        )

    async def _ao_pedir_foto(self, evento) -> None:
        """Atende um pedido EXPLÍCITO de foto (Vigília ou ação da IA).

        Três decisões que valem explicar:

        - **não respeita o cooldown**: ele existe para não repetir alerta
          enquanto o mesmo estranho continua no quadro. Aqui alguém PEDIU a
          foto; recusar seria ignorar uma ordem por causa de uma regra que
          trata de outro assunto.
        - **atende mesmo pausado**: o alívio de carga pausa os alertas porque
          reconhecimento facial é caro. Gravar um frame que já está na memória
          é um `imwrite` - não é o que sobrecarrega o Notebook.
        - **não publica evento de volta.** Seria fácil anunciar
          `sentinela.foto_salva`, e seria exatamente o bug que este handler
          corrige: um tópico sem assinante. Pior, um evento daqui que voltasse
          como `sentinela.alerta` faria a Vigília pedir foto outra vez, em
          laço. O caminho fica no log, que é quem hoje tem leitor.
        """
        motivo = evento.dados.get("motivo", "sem_motivo")
        caminho = self._pedir_foto(prefixo=motivo)
        if caminho is None:
            logger.warning("pedido de foto '%s' NÃO gravou (sem frame?)", motivo)
            return
        logger.info("foto gravada a pedido de '%s': %s", motivo, caminho)

    def _pedir_foto(self, prefixo: str = "estranho") -> str | None:
        """Falha ao salvar a foto NÃO cancela o alerta: saber que há um
        estranho vale mais que ter a imagem dele.

        `prefixo` entra no nome do arquivo para que, olhando a pasta, se saiba
        POR QUE cada foto existe - alerta de rosto desconhecido, vigília ou
        decisão da IA saem com nomes diferentes.
        """
        if self._salvar_foto is None:
            return None
        seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefixo) or "foto"
        nome = f"{seguro}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        try:
            return self._salvar_foto(str(self._pasta_fotos / nome))
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao salvar a foto ('%s')", prefixo, exc_info=True)
            return None
