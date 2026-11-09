"""Orquestrador do Vision Core (Cap 8).

Liga captura, deteccao YOLO, reconhecimento facial, rastreamento e
Pan/Tilt num unico pipeline, publicando eventos no Event Bus local do
Notebook (Cap 8 s.5). O Vision Core nunca toma decisao estrategica (Cap 8
s.6) - so publica informacao estruturada.

Recuperacao de camera desconectada (Cap 8 s.9): ao falhar, publica
vision.camera_error e tenta reabrir periodicamente, mantendo o resto do
ORION OS operacional (modo SEM_VISAO) em vez de derrubar o processo.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

import cv2

from orion.kernel.event_bus import EventBus, Prioridade
from orion.vision.captura import CapturaCamera, ErroCamera
from orion.vision.deteccao import Deteccao, DetectorYolo
from orion.vision.olhar import ConfigOlhar, Procura, ponto_de_mira
from orion.vision.pan_tilt import CalculadoraPanTilt, LimitesPanTilt
from orion.vision.rastreamento import Caixa, Rastreador
from orion.vision.reconhecimento_facial import ReconhecedorFacial, RostoReconhecido
from orion.vision.sem_sinal import DetectorSemSinal

logger = logging.getLogger("orion.vision.vision_core")

CallbackPanTilt = Callable[[float, float], Awaitable[None]]

#: Idade maxima da ultima cena para `ver_agora()` valer como "o que estou
#: vendo AGORA" (2026-08-09). O laco processa na casa de alguns quadros por
#: segundo, entao 3 s = muitas cenas - folga para um Notebook ocupado. Alem
#: disso a visao NAO esta mais vendo de verdade (pausada pelo alivio de
#: carga, camera engasgada) e responder do snapshot antigo seria mentir.
TEMPO_MAXIMO_CENA_S = 3.0

#: "O que voce esta vendo" responde da JANELA dos ultimos segundos, nao de um
#: quadro so (2026-11-01). Com imgsz 320, objeto pequeno (copo, celular,
#: controle) aparece num quadro e some no seguinte; se a pergunta caisse no
#: quadro vazio, a resposta era "nada". Entra na resposta o que apareceu em
#: pelo menos PRESENCAS_MINIMAS quadros da janela - um quadro so pode ser
#: engano do modelo.
JANELA_CENA_S = 3.0
PRESENCAS_MINIMAS = 2

#: Marca os `motion.pan_tilt` que ESTA classe publicou, para nao ressincronizar
#: a estimativa com o proprio eco (ver `_ao_outro_dono_mover_a_cabeca`).
#:
#: So a visao se identifica, de proposito: quem NAO se identifica e tratado
#: como outro dono. Assim qualquer publicador futuro de `motion.pan_tilt` passa
#: a ressincronizar a visao sem precisar saber que esta calculadora existe -
#: o default fica no lado correto.
ORIGEM_VISAO = "visao"


class VisionCore:
    def __init__(
        self,
        event_bus: EventBus,
        indice_camera: int | str = 0,
        camera_espelhada: bool = False,
        confianca_minima: float = 0.55,
        modelo_yolo: str = "yolov8n.pt",
        limites_pan_tilt: LimitesPanTilt | None = None,
        publicar_pan_tilt: CallbackPanTilt | None = None,
        captura: CapturaCamera | None = None,
        detector: DetectorYolo | None = None,
        reconhecedor: ReconhecedorFacial | None = None,
        timeout_alvo_perdido_s: float = 2.0,
        frames_confirmacao_alvo: int = 1,
        fps: float = 0.0,
        imgsz: int = 640,
        reconhecer_rosto_a_cada: int = 1,
        confianca_para_descrever: float | None = None,
        olhar: ConfigOlhar | None = None,
    ) -> None:
        # captura/detector/reconhecedor sao injetaveis para testar a logica
        # de orquestracao (associacao de identidade, eventos, rastreamento)
        # sem precisar de camera/modelos de verdade.
        self._event_bus = event_bus
        self._captura = captura or CapturaCamera(indice=indice_camera, espelhado=camera_espelhada)
        #: Quem quer ver os frames sem abrir a camera (EDR-0023 s.3: so o
        #: dono da camera tem o frame). Hoje: o detector de gestos.
        self._observadores_de_frame: list = []
        # modelo_yolo vem do orion.yaml (vision.yolo_model). Antes o nome do
        # modelo ficava fixo no default do DetectorYolo e a config era
        # ignorada - configuracao morta, contra a regra 6 do CLAUDE.md.
        # DOIS LIMIARES (2026-11-01). `confianca_minima` (0,55) decide o que
        # vira EVENTO, chega ao rastreador e move a cabeca - ali um falso
        # positivo custa caro. `confianca_para_descrever` (0,35) decide so o
        # que entra na resposta "o que voce esta vendo" - ali 0,55 era
        # rigoroso demais e calava objeto pequeno. O detector solta tudo a
        # partir do MENOR dos dois; o corte de acao e feito num ponto so,
        # logo depois do `detectar()`, para nenhum assinante novo herdar o
        # limiar baixo sem querer.
        self._confianca_acao = confianca_minima
        self._confianca_descrever = min(
            confianca_para_descrever if confianca_para_descrever is not None else confianca_minima,
            confianca_minima,
        )
        self._detector = detector or DetectorYolo(
            modelo=modelo_yolo, confianca_minima=self._confianca_descrever, imgsz=imgsz
        )
        self._reconhecedor = reconhecedor or ReconhecedorFacial()
        self._rastreador = Rastreador(
            timeout_perdido_s=timeout_alvo_perdido_s, frames_confirmacao=frames_confirmacao_alvo
        )
        self._pan_tilt = CalculadoraPanTilt(limites_pan_tilt)
        self._publicar_pan_tilt = publicar_pan_tilt
        self._executando = False
        #: Pausa o PROCESSAMENTO sem fechar a camera (alivio de carga,
        #: EDR-0020). Fechar o dispositivo seria pior: reabrir custa segundos
        #: e nem sempre da certo, e o objetivo e passar por um aperto de
        #: memoria, nao perder a visao de vez.
        self._pausado = False
        self._tinha_alvo = False
        #: A cada quantos quadros rodar o reconhecimento facial (Cap 8 s.4).
        #:
        #: 1 = todo quadro, o comportamento anterior a 2026-07-30. Vem de
        #: `vision.face_recognition_every_n_frames` no orion.yaml, onde estao
        #: as medicoes que justificam o valor. Resumo: o reconhecimento custa
        #: 442-716 ms contra 125 ms do YOLO, e nao da para baratea-lo sem
        #: perder rosto - entao o que muda e o RITMO, nao a qualidade.
        #:
        #: `max(1, ...)` porque 0 ou negativo aqui nao significa nada e
        #: dividiria por zero no `%` abaixo.
        self._reconhecer_a_cada = max(1, int(reconhecer_rosto_a_cada))
        self._quadros_processados = 0
        #: Ultimo resultado do reconhecimento, reaproveitado nos quadros em
        #: que ele nao roda. NAO e cache de otimizacao: e o que impede a
        #: identidade de piscar. Devolver lista vazia nos quadros
        #: intermediarios faria o alvo perder o nome e voltar a te-lo, e o
        #: FOLLOW (que so segue gente RECONHECIDA) largaria a pessoa a cada
        #: dois quadros.
        self._ultimos_rostos: list[RostoReconhecido] = []
        self._ultimo_processamento_ts: float | None = None
        self._ultimo_frame = None
        #: Estado da ultima cena processada - o "o que voce esta vendo AGORA".
        #:
        #: A cadeia antiga so tinha o diario: a visao via, o diario guardava e
        #: NINGUEM consultava o momento. Perguntado "o que e isso?", o robo
        #: respondia do que viu ha meia hora, ou a IA chutava no escuro (o
        #: modelo nao ve o frame). Este dicionario e o que quem pergunta le
        #: em vez disso - preenchido a cada quadro e consultavel por
        #: `ver_agora()`.
        self._ultima_cena: dict | None = None
        #: As cenas da janela de `JANELA_CENA_S` - de onde `ver_agora()` tira
        #: a resposta. A 2 quadros/s cabem ~6; 30 e folga para fps maior.
        self._cenas_recentes: deque[dict] = deque(maxlen=30)
        #: Camera que entrega imagem mas nao enxerga (tela "NO SIGNAL", lente
        #: tampada, quadro congelado) - ver `sem_sinal.py`.
        self._sem_sinal = DetectorSemSinal()
        self._sem_sinal_reportado = False
        # 0 = sem limite (comportamento antigo). Vem de vision.fps no
        # orion.yaml: ate 2026-07-25 essa chave era configuracao MORTA e o
        # laco rodava a toda velocidade, medido em 131% de CPU disputando os
        # 4 nucleos com o Whisper. Ver _dormir_o_resto_do_quadro.
        self._intervalo_alvo_s = 1.0 / fps if fps and fps > 0 else 0.0
        # A cabeca tem DOIS donos e ate 2026-07-29 este modulo nao tinha um
        # unico `subscribe`: ele mandava SET_PAN_TILT e assumia que ninguem
        # mais tocava nos servos. Os comportamentos do Pi tocam. Assinar aqui
        # e o que impede a estimativa de virar ficcao - ver
        # CalculadoraPanTilt.sincronizar.
        event_bus.subscribe("motion.pan_tilt", self._ao_outro_dono_mover_a_cabeca)
        # PARA ONDE OLHAR (2026-11-08, ver vision/olhar.py). None = o
        # comportamento antigo: mira o centro da caixa e nao procura.
        self._olhar = olhar
        self._procura = Procura(olhar) if olhar is not None else None
        self._sem_alvo_desde: float | None = None
        self._procurando = False
        #: Quando OUTRO dono (explore, ronda, joystick, conversa) mexeu na
        #: cabeca pela ultima vez. A procura cede a vez por
        #: `ceder_a_outro_dono_s` - sem isso ela brigaria com quem esta usando.
        self._outro_dono_ts = float("-inf")
        #: EM MANUAL A CABECA E DO OPERADOR (decisao do dono, 2026-08-04). O
        #: Pi ja respeitava isso no caminho dele; a visao mandava o servo
        #: direto e ignorava. Agora ela continua vendo e informando, mas nao
        #: mexe na cabeca - nem para seguir, nem para procurar.
        self._modo_manual = False
        event_bus.subscribe("behavior.modo_controle", self._ao_mudar_modo_controle)

    async def _ao_outro_dono_mover_a_cabeca(self, evento) -> None:
        """Ressincroniza a posicao assumida dos servos.

        Ignora o proprio eco: esta classe publica `motion.pan_tilt` a cada
        quadro com alvo, e o Pi repassa os DELE para ca - as duas coisas
        chegam no mesmo topico do mesmo barramento.
        """
        if evento.dados.get("origem") == ORIGEM_VISAO:
            return
        pan = evento.dados.get("pan")
        tilt = evento.dados.get("tilt")
        if pan is None or tilt is None:
            return
        try:
            self._pan_tilt.sincronizar(pan, tilt)
        except (TypeError, ValueError):
            # payload de outra maquina: dado ruim nao pode derrubar o handler.
            logger.warning("motion.pan_tilt com pan/tilt invalido: %r", evento.dados)
            return
        self._outro_dono_ts = time.monotonic()
        logger.debug("pan/tilt ressincronizado por outro dono: %s, %s", pan, tilt)

    async def _ao_mudar_modo_controle(self, evento) -> None:
        """`behavior.modo_controle` vem do Pi (repassado pelo enlace)."""
        manual = evento.dados.get("modo") == "manual"
        if manual != self._modo_manual:
            logger.info(
                "visao: modo %s - %s",
                evento.dados.get("modo"),
                "a cabeca e do operador" if manual else "a visao volta a mover a cabeca",
            )
        self._modo_manual = manual

    def salvar_ultimo_frame(self, caminho: str) -> str | None:
        """Grava em disco a ultima imagem lida. Devolve o caminho, ou None se
        ainda nao houve frame (camera ausente ou recem-aberta).

        Existe aqui porque so o dono da camera tem o frame: quem precisa da
        foto pede, em vez de abrir o dispositivo por conta propria.
        """
        if self._ultimo_frame is None:
            return None
        cv2.imwrite(caminho, self._ultimo_frame)
        return caminho

    def ultimo_frame_jpeg(self, qualidade: int = 80) -> bytes | None:
        """A ultima imagem lida, em JPEG, para o snapshot da API (2026-11-01).

        Mesmo motivo do `salvar_ultimo_frame`: a camera USB so aceita um
        leitor, entao quem quer ver a imagem PEDE ao dono em vez de abrir o
        dispositivo. None se ainda nao houve frame.
        """
        if self._ultimo_frame is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", self._ultimo_frame, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
        return jpeg.tobytes() if ok else None

    def ver_agora(self) -> dict | None:
        """O que a camera ve neste instante - a janela dos ultimos segundos.

        O caminho que faltava para "o que e isso?": a visao via, o diario
        guardava, e nada consultava o AGORA. Devolve o estado da ultima
        cena, para quem pergunta conversar com a camera de verdade e nao
        com o diario de meia hora atras.

        Devolve uma copia (ninguem pode mexer no estado interno ao ler).
        None = ainda nao houve cena, ou a cena ja ficou velha demais
        (`TEMPO_MAXIMO_CENA_S`) - a visao nao esta vendo de verdade agora,
        e responder do que viu ha mais tempo seria inventar.
        """
        if self._ultima_cena is None:
            return None
        agora = time.monotonic()
        idade = agora - self._ultima_cena.get("timestamp", 0.0)
        if idade > TEMPO_MAXIMO_CENA_S:
            return None
        janela = [
            c for c in self._cenas_recentes if agora - c["timestamp"] <= JANELA_CENA_S
        ] or [self._ultima_cena]
        return self._agregar_cenas(janela)

    @staticmethod
    def _agregar_cenas(cenas: list[dict]) -> dict:
        """Junta as cenas da janela numa so, no MESMO formato de uma cena.

        Objeto entra se apareceu em `PRESENCAS_MINIMAS` quadros. Com menos
        quadros que isso na janela (visao lenta, recem-ligada), basta um -
        senao a visao lenta responderia sempre "nada". `pessoas` e o maior
        numero de pessoas visto num quadro; `reconhecidos` e a uniao dos
        nomes; `desconhecidos` vem do quadro mais recente, porque o rosto e
        reaproveitado entre quadros e somar contaria a mesma cara varias vezes.
        """
        minimo = PRESENCAS_MINIMAS if len(cenas) >= PRESENCAS_MINIMAS else 1
        presencas: dict[str, int] = {}
        melhor: dict[str, float] = {}
        for cena in cenas:
            for classe in {o["classe"] for o in cena.get("objetos", [])}:
                presencas[classe] = presencas.get(classe, 0) + 1
            for o in cena.get("objetos", []):
                melhor[o["classe"]] = max(melhor.get(o["classe"], 0.0), o["confianca"])
        objetos = [
            {"classe": classe, "confianca": melhor[classe]}
            for classe in sorted(presencas, key=lambda c: -melhor[c])
            if presencas[classe] >= minimo
        ]
        reconhecidos: list[dict] = []
        vistos: set = set()
        for cena in cenas:
            for r in cena.get("reconhecidos", []):
                if r["pessoa_id"] not in vistos:
                    vistos.add(r["pessoa_id"])
                    reconhecidos.append(dict(r))
        pessoas = 0
        if any(o["classe"] == "person" for o in objetos):
            pessoas = max(c.get("pessoas", 0) for c in cenas)
        mais_recente = max(cenas, key=lambda c: c["timestamp"])
        return copy.deepcopy(
            {
                "objetos": objetos,
                "reconhecidos": reconhecidos,
                "desconhecidos": mais_recente.get("desconhecidos", 0),
                "pessoas": pessoas,
                "quadros": len(cenas),
                "timestamp": mais_recente["timestamp"],
            }
        )

    def carregar_pessoas_conhecidas(self, pessoas: list[dict]) -> None:
        self._reconhecedor.carregar_pessoas_conhecidas(pessoas)

    async def iniciar_camera(self) -> None:
        try:
            await self._captura.abrir()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

    def registrar_observador_de_frame(self, callback) -> None:
        """Recebe cada frame lido, sem abrir um segundo leitor do device.

        Existe porque o EDR-0023 fez o ModuloVisao dono da camera, e o
        detector de gestos - que ANTES era a fonte de frames da casa - ficou
        orfao: a classe existia, com testes e modelo baixado, e nunca era
        instanciada. Nenhum gesto chegou a ser produzido em producao.
        """
        self._observadores_de_frame.append(callback)

    async def processar_um_frame(self):
        """Uma iteracao do pipeline (Cap 8 s.3). Retorna o frame lido (BGR,
        formato OpenCV) - util para quem quiser exibir/depurar."""
        agora = time.monotonic()
        intervalo_s = (
            agora - self._ultimo_processamento_ts if self._ultimo_processamento_ts else 0.1
        )
        self._ultimo_processamento_ts = agora

        try:
            frame = await self._captura.ler_frame()
        except ErroCamera as erro:
            await self._event_bus.publish(
                "vision.camera_error", {"motivo": str(erro)}, prioridade=Prioridade.ALTA
            )
            raise

        # Observadores do frame (EDR-0023 s.3: só o dono da câmera tem o
        # frame). Existe para o detector de gestos poder trabalhar sem abrir
        # um segundo leitor do dispositivo. Falha de observador NUNCA derruba
        # a visão - é acessório reagindo a um frame que já foi lido.
        for observador in self._observadores_de_frame:
            try:
                await observador(frame)
            except Exception:
                logger.debug("observador de frame falhou", exc_info=True)

        # CAMERA CEGA GRITA (2026-11-01). Antes do YOLO: quadro escuro e
        # uniforme, ou congelado, por varios quadros seguidos = a camera nao
        # esta enxergando. Publica `vision.camera_error` UMA vez (na borda) e
        # nao roda o YOLO sobre a tela preta - era assim que a visao ficava
        # "saudavel" e cega ao mesmo tempo.
        self._ultimo_frame = frame
        avaliacao = self._sem_sinal.avaliar(frame)
        if avaliacao.sem_sinal:
            if not self._sem_sinal_reportado:
                self._sem_sinal_reportado = True
                logger.error(
                    "camera SEM SINAL (%s: brilho %.1f, desvio %.1f, diferenca %s) - "
                    "a visao nao esta enxergando",
                    avaliacao.motivo,
                    avaliacao.brilho_medio,
                    avaliacao.desvio,
                    avaliacao.diferenca_anterior,
                )
                await self._event_bus.publish(
                    "vision.camera_error",
                    {"motivo": f"sem_sinal:{avaliacao.motivo}"},
                    prioridade=Prioridade.ALTA,
                )
            self._ultima_cena = None
            self._cenas_recentes.clear()
            return frame
        if self._sem_sinal_reportado:
            self._sem_sinal_reportado = False
            logger.info("camera voltou a enxergar")

        # O CORTE DE ACAO E AQUI, num ponto so: `deteccoes` (>= limiar de
        # acao) alimenta rastreador, cabeca, identidades e eventos;
        # `para_descrever` (>= limiar de descrever) alimenta SO a cena.
        para_descrever = await self._detector.detectar(frame)
        deteccoes = [d for d in para_descrever if d.confianca >= self._confianca_acao]
        para_descrever = [d for d in para_descrever if d.confianca >= self._confianca_descrever]
        pessoas_detectadas = DetectorYolo.pessoas(deteccoes)

        # O RECONHECIMENTO FACIAL TEM RITMO PROPRIO (2026-07-30), porque ele
        # custa 442-716 ms contra 125 ms do YOLO e nao da para baratea-lo sem
        # perder rosto (as medicoes estao no orion.yaml, em
        # `face_recognition_every_n_frames`). Com `_reconhecer_a_cada = 1` o
        # comportamento e exatamente o de antes.
        #
        # O contador incrementa DEPOIS do teste para que o primeiro quadro
        # sempre reconheca: comecar a vida sem identidade nenhuma por N
        # quadros e o pior momento para economizar.
        reconheceu_agora = (self._quadros_processados % self._reconhecer_a_cada) == 0
        self._quadros_processados += 1
        if reconheceu_agora:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._ultimos_rostos = await self._reconhecedor.reconhecer(frame_rgb)
        rostos = self._ultimos_rostos
        # O frame do momento (a Sentinela salva a foto do estranho) ja foi
        # guardado la em cima, antes da checagem de sinal.

        # Rostos que nao casaram com ninguem conhecido. A Sentinela consome
        # este evento em vez de abrir a propria camera e rodar um SEGUNDO
        # reconhecimento facial sobre os mesmos frames - era o trabalho mais
        # pesado do Notebook, feito em dobro ate 2026-07-25.
        #
        # `and reconheceu_agora`: so anuncia desconhecido quando ACABOU de
        # olhar. Sem isso, um mesmo rosto reaproveitado dispararia o evento a
        # cada quadro e a Sentinela salvaria a mesma cara N vezes - ela ja
        # tinha 37 fotos acumuladas em data/sentinela antes desta mudanca.
        desconhecidos = [rosto for rosto in rostos if rosto.pessoa_id is None]
        if desconhecidos and reconheceu_agora:
            await self._event_bus.publish(
                "vision.faces_desconhecidas",
                {"quantidade": len(desconhecidos), "timestamp": agora},
                prioridade=Prioridade.ALTA,
            )

        for deteccao in deteccoes:
            topico = "vision.person_detected" if deteccao.classe == "person" else "vision.object_detected"
            await self._event_bus.publish(
                topico,
                {
                    "classe": deteccao.classe,
                    "confianca": deteccao.confianca,
                    "caixa": deteccao.caixa,
                    "timestamp": agora,
                },
            )

        identidades = self._associar_identidades(pessoas_detectadas, rostos)
        # O AGORA consultavel (2026-08-09): quem pergunta "o que e isso?"
        # le este estado, preenchido a cada quadro. Objetos + quem foi
        # reconhecido + quantos estranhos - o que a camera ve, resumido.
        self._ultima_cena = {
            "objetos": [
                {"classe": d.classe, "confianca": round(float(d.confianca), 2)}
                for d in para_descrever
            ],
            "reconhecidos": [
                {"nome": nome, "pessoa_id": pessoa_id}
                for pessoa_id, nome in identidades.values()
                if pessoa_id is not None and nome
            ],
            "desconhecidos": len(desconhecidos),
            "pessoas": sum(1 for d in para_descrever if d.classe == "person"),
            "timestamp": agora,
        }
        self._cenas_recentes.append(self._ultima_cena)
        caixas = [d.caixa for d in pessoas_detectadas]
        alvo = self._rastreador.atualizar(caixas, identidades, agora=agora)

        if alvo is not None:
            self._tinha_alvo = True
            self._sem_alvo_desde = None
            if self._procurando:
                self._procurando = False
                logger.info("visao: achei alguem - parei de procurar")
            altura, largura = frame.shape[:2]
            cx, cy = self._centro_caixa(alvo.caixa)
            # Onde CENTRALIZAR: o rosto, nao a barriga (vision/olhar.py). O
            # `cx` do corpo continua indo no evento - e o lado da pessoa que o
            # FOLLOW usa, e para isso o corpo e a melhor referencia.
            mira_x, mira_y = cx, cy
            if self._olhar is not None and self._olhar.mirar_no_rosto:
                mira_x, mira_y = ponto_de_mira(
                    alvo.caixa,
                    [rosto.caixa for rosto in rostos],
                    self._olhar.fracao_altura_cabeca,
                )
            if alvo.pessoa_id is not None:
                # centro_x normalizado (0..1, 0.5 = centralizado): o FOLLOW
                # do NavigationCore (Pi) so anda atras de gente RECONHECIDA -
                # por isso ele escuta este evento, e nao vision.person_detected
                # (que dispara para qualquer pessoa, reconhecida ou nao).
                await self._event_bus.publish(
                    "vision.person_recognized",
                    {
                        "pessoa_id": alvo.pessoa_id,
                        "nome": alvo.nome,
                        "centro_x": cx / largura,
                        "timestamp": agora,
                    },
                )
            if self._modo_manual:
                pan, tilt = self._pan_tilt.pan_atual, self._pan_tilt.tilt_atual
            else:
                pan, tilt = self._pan_tilt.calcular_correcao(
                    mira_x, mira_y, largura, altura, intervalo_s
                )
                # publica sempre (mesmo sem callback de hardware) - o avatar
                # (Cap 13) reflete a direcao calculada independente do robo
                # fisico estar montado ou nao.
                await self._event_bus.publish(
                    "motion.pan_tilt", {"pan": pan, "tilt": tilt, "origem": ORIGEM_VISAO}
                )
                if self._publicar_pan_tilt is not None:
                    await self._publicar_pan_tilt(pan, tilt)

            if self._pan_tilt.alvo_centralizado(mira_x, mira_y, largura, altura):
                await self._event_bus.publish(
                    "vision.target_centered", {"pan": pan, "tilt": tilt, "timestamp": agora}
                )
        else:
            if self._tinha_alvo:
                await self._event_bus.publish("vision.person_lost", {"timestamp": agora})
                self._tinha_alvo = False
            await self._talvez_procurar(agora, intervalo_s)

        return frame

    async def _talvez_procurar(self, agora: float, intervalo_s: float) -> None:
        """Sem ninguem no quadro, varre devagar com a camera erguida.

        So mexe quando TUDO isto vale - cada condicao protege alguem:
        - `cabeca_segue_pessoa` ligado (sem callback de servo, nao ha cabeca);
        - fora do MANUAL (a cabeca e do operador);
        - `procurar_apos_s` sem alvo (nao sai procurando por 1 quadro ruim);
        - ninguem mais mexeu na cabeca nos ultimos `ceder_a_outro_dono_s`
          (o explore apontando para o giro, a ronda, o joystick).
        """
        if self._olhar is None or self._procura is None or not self._olhar.procurar:
            return
        if self._publicar_pan_tilt is None or self._modo_manual:
            return
        if self._sem_alvo_desde is None:
            self._sem_alvo_desde = agora
            return
        if agora - self._sem_alvo_desde < self._olhar.procurar_apos_s:
            return
        if agora - self._outro_dono_ts < self._olhar.ceder_a_outro_dono_s:
            return
        if not self._procurando:
            self._procurando = True
            logger.info(
                "visao: ninguem no quadro ha %.0f s - procurando", self._olhar.procurar_apos_s
            )
        pan, tilt = self._procura.proximo(
            self._pan_tilt.pan_atual, self._pan_tilt.tilt_atual, intervalo_s
        )
        self._pan_tilt.sincronizar(pan, tilt)
        await self._event_bus.publish(
            "motion.pan_tilt", {"pan": pan, "tilt": tilt, "origem": ORIGEM_VISAO}
        )
        await self._publicar_pan_tilt(pan, tilt)

    async def executar(self, intervalo_reconexao_s: float = 3.0) -> None:
        """Loop continuo ate parar() ser chamado. Reconecta automaticamente
        se a camera cair (Cap 8 s.9)."""
        self._executando = True
        while self._executando:
            if self._pausado:
                # Nao processa quadro nenhum: e aqui que o alivio de carga
                # economiza de verdade. O YOLO e o face_recognition sao o
                # trabalho mais pesado do Notebook, e ate 2026-07-30 o alivio
                # nunca chegava a pausa-los - `pausar_visao` era passado como
                # None em conversar_jarvis.py.
                await asyncio.sleep(0.5)
                continue
            if not self._captura.aberta:
                try:
                    await self.iniciar_camera()
                except ErroCamera:
                    await asyncio.sleep(intervalo_reconexao_s)
                    continue
            inicio = time.monotonic()
            try:
                await self.processar_um_frame()
            except ErroCamera:
                await self._captura.fechar()
                await asyncio.sleep(intervalo_reconexao_s)
                continue
            await self._dormir_o_resto_do_quadro(inicio)

    async def _dormir_o_resto_do_quadro(self, inicio: float) -> None:
        """Devolve ao sistema o tempo que sobrou do orcamento do quadro.

        Sem isto o laco processa o mais rapido que a CPU aguentar - e num
        Notebook de 4 nucleos onde o Whisper tambem precisa de CPU, isso
        engasga a voz. Se o quadro ja estourou o orcamento, nao dorme (mas
        cede a vez com sleep(0), senao um quadro lento monopoliza o loop de
        eventos e ninguem mais roda).
        """
        if not self._intervalo_alvo_s:
            return
        sobra = self._intervalo_alvo_s - (time.monotonic() - inicio)
        await asyncio.sleep(sobra if sobra > 0 else 0)

    def parar(self) -> None:
        self._executando = False

    def pausar(self) -> None:
        """Suspende o processamento, mantendo a camera aberta."""
        if not self._pausado:
            self._pausado = True
            logger.warning("Vision Core PAUSADO (alivio de carga) - camera segue aberta")

    def retomar(self) -> None:
        if self._pausado:
            self._pausado = False
            logger.info("Vision Core retomado")

    @property
    def pausado(self) -> bool:
        return self._pausado

    @staticmethod
    def _centro_caixa(caixa: Caixa) -> tuple[float, float]:
        x1, y1, x2, y2 = caixa
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _associar_identidades(
        pessoas_detectadas: list[Deteccao], rostos: list[RostoReconhecido]
    ) -> dict[Caixa, tuple[int | None, str | None]]:
        """Casa cada caixa de pessoa (YOLO) com o rosto (face_recognition)
        de maior sobreposicao - vem de dois modelos diferentes, cada um
        com sua propria caixa."""
        identidades: dict[Caixa, tuple[int | None, str | None]] = {}
        for pessoa in pessoas_detectadas:
            melhor: RostoReconhecido | None = None
            melhor_sobreposicao = 0.0
            for rosto in rostos:
                sobreposicao = VisionCore._sobreposicao(pessoa.caixa, rosto.caixa)
                if sobreposicao > melhor_sobreposicao:
                    melhor_sobreposicao = sobreposicao
                    melhor = rosto
            if melhor is not None and melhor_sobreposicao > 0:
                identidades[pessoa.caixa] = (melhor.pessoa_id, melhor.nome)
        return identidades

    @staticmethod
    def _sobreposicao(caixa_a: Caixa, caixa_b: Caixa) -> float:
        ax1, ay1, ax2, ay2 = caixa_a
        bx1, by1, bx2, by2 = caixa_b
        largura_i = max(0, min(ax2, bx2) - max(ax1, bx1))
        altura_i = max(0, min(ay2, by2) - max(ay1, by1))
        return float(largura_i * altura_i)
