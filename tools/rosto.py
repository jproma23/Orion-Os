"""O rosto leve do robo: tela preta, dois olhos e uma boca (2026-11-12).

Substitui o Firefox em modo kiosk (dono: "a tela kiosk consome bastante RAM").
Um processo pygame pequeno que:

- ouve os eventos que o robo ja publica, pelo MESMO SSE que o avatar servia
  (`http://127.0.0.1:8090/eventos`, do `orion-avatar`) - o resto do robo nao
  muda, e se o servico reiniciar, este reconecta sozinho;
- decide a cara em `orion/display/rosto_estado.py` e pinta em
  `orion/display/rosto_desenho.py`.

Uso (no Notebook, com o `orion-avatar` no ar):

    .venv/bin/python tools/rosto.py            # tela cheia (Esc ou q fecha)
    .venv/bin/python tools/rosto.py --janela   # janela 960x540, para testar
    .venv/bin/python tools/rosto.py --demo     # sem o robo: passa pelas expressoes

O autostart do kiosk fica em `tools/orion-rosto-kiosk.desktop`.
"""
from __future__ import annotations

import argparse
import json
import logging
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pygame  # noqa: E402

from orion.display.rosto_desenho import desenhar  # noqa: E402
from orion.display.rosto_estado import EstadoRosto  # noqa: E402

logger = logging.getLogger("orion.rosto")

URL_PADRAO = "http://127.0.0.1:8090/eventos"
FPS = 30
ESPERA_RECONEXAO_S = 2.0

#: O navegador do QUADRO, so durante a aula (dono, 12/11: "abre o firefox so
#: durante a aula"). Mesmo perfil e mesma pagina do kiosk antigo.
COMANDO_NAVEGADOR = (
    'mkdir -p "$HOME/.mozilla/firefox/orion-avatar-kiosk" && exec firefox '
    '--profile "$HOME/.mozilla/firefox/orion-avatar-kiosk" --no-remote --kiosk '
    "http://127.0.0.1:8090"
)
#: Depois do fim da aula, o quadro fica este tempo para o fechamento ser lido.
GRACA_FIM_AULA_S = 10.0
#: Se o navegador cair no meio da aula, espera isto antes de reabrir.
ESPERA_REABRIR_S = 10.0


class NavegadorDaAula:
    """Abre o Firefox do quadro no comeco da aula e fecha no fim.

    O resto do tempo a tela e so o rosto - o Firefox era o maior consumidor
    do Notebook. `lancar` e injetavel: devolve um objeto com poll()/terminate()
    /wait()/kill() (um subprocess.Popen), e nos testes e um falso.
    """

    def __init__(self, lancar=None) -> None:
        self._lancar = lancar or (lambda: subprocess.Popen(["sh", "-c", COMANDO_NAVEGADOR]))
        self._processo = None
        self._em_aula = False
        self._fechar_em: float | None = None
        self._aberto_em = float("-inf")

    @property
    def aberto(self) -> bool:
        return self._processo is not None and self._processo.poll() is None

    def ao_evento(self, topico: str, dados: dict, agora: float) -> None:
        if topico != "display.aula" or not isinstance(dados, dict):
            return
        estado = dados.get("estado")
        if estado == "comecou":
            self._em_aula = True
            self._fechar_em = None
            self._abrir(agora)
        elif estado == "terminou":
            self._em_aula = False
            self._fechar_em = agora + GRACA_FIM_AULA_S

    def tique(self, agora: float) -> None:
        if self._fechar_em is not None and agora >= self._fechar_em:
            self._fechar_em = None
            self.fechar()
        elif self._em_aula and not self.aberto and agora - self._aberto_em >= ESPERA_REABRIR_S:
            logger.warning("navegador do quadro fechou no meio da aula - reabrindo")
            self._abrir(agora)

    def _abrir(self, agora: float) -> None:
        if self.aberto:
            return
        try:
            self._processo = self._lancar()
            self._aberto_em = agora
            logger.info("aula: navegador do quadro aberto")
        except Exception:  # noqa: BLE001
            logger.exception("nao consegui abrir o navegador do quadro")

    def fechar(self) -> None:
        if not self.aberto:
            self._processo = None
            return
        self._processo.terminate()
        try:
            self._processo.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self._processo.kill()
        self._processo = None
        logger.info("aula: navegador do quadro fechado - volta o rosto")


def ler_sse(linhas, entregar) -> None:
    """Le um fluxo SSE (`event: x` / `data: {...}` / linha vazia) e entrega
    (topico, dados). Separado da rede para ser testavel com uma lista."""
    topico, dados = None, []
    for bruta in linhas:
        linha = bruta.decode("utf-8", "replace") if isinstance(bruta, bytes) else bruta
        linha = linha.rstrip("\r\n")
        if not linha:
            if topico and dados:
                try:
                    entregar(topico, json.loads("\n".join(dados)))
                except ValueError:
                    logger.debug("evento ilegivel: %s", topico)
            topico, dados = None, []
        elif linha.startswith(":"):
            continue  # comentario/ping do servidor
        elif linha.startswith("event:"):
            topico = linha[6:].strip()
        elif linha.startswith("data:"):
            dados.append(linha[5:].strip())


def ouvir_para_sempre(url: str, fila: queue.Queue, parar: threading.Event) -> None:
    """Thread: conecta no SSE e reconecta sempre que cair (o servico reinicia)."""
    while not parar.is_set():
        try:
            with urllib.request.urlopen(url, timeout=30) as resposta:
                logger.info("rosto conectado em %s", url)
                ler_sse(resposta, lambda t, d: fila.put((t, d)))
        except Exception as erro:  # noqa: BLE001
            logger.debug("SSE caiu (%r) - reconectando", erro)
        parar.wait(ESPERA_RECONEXAO_S)


#: Um "envelope" inventado so para o --demo mexer a boca sem o robo.
FALA_DE_MENTIRA = [0, 0.4, 0.9, 0.7, 0.2, 0, 0.5, 1, 0.8, 0.3] * 8


def roteiro_demo(inicio: float, agora: float) -> list[tuple[str, dict]]:
    """Uma passada pelas expressoes, para ver o rosto sem o robo."""
    passos = [
        (0.0, "voice.status", {"estado": "IDLE"}),
        (3.0, "voice.status", {"estado": "LISTENING"}),
        (5.0, "voice.status", {"estado": "THINKING"}),
        (7.0, "voice.status", {"estado": "SPEAKING"}),
        (7.0, "voice.fala_envelope", {"passo_s": 0.04, "niveis": FALA_DE_MENTIRA}),
        (11.0, "voice.status", {"estado": "IDLE"}),
        (11.5, "vision.person_recognized", {"centro_x": 0.85}),
        (15.0, "motion.obstacle_front", {}),
        (17.0, "voice.status", {"estado": "ERROR"}),
        (20.0, "voice.status", {"estado": "IDLE"}),
    ]
    return [(t, d) for quando, t, d in passos if roteiro_demo.ultimo < quando <= agora - inicio]


roteiro_demo.ultimo = -1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rosto leve do robo (pygame)")
    parser.add_argument("--url", default=URL_PADRAO)
    parser.add_argument("--janela", action="store_true", help="janela 960x540 em vez de tela cheia")
    parser.add_argument("--demo", action="store_true", help="sem o robo: passa pelas expressoes")
    parser.add_argument(
        "--sem-quadro", action="store_true", help="nao abre o Firefox do quadro nas aulas"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    pygame.init()
    if args.janela:
        tela = pygame.display.set_mode((960, 540))
    else:
        tela = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)
    pygame.display.set_caption("Jarvis")

    fila: queue.Queue = queue.Queue()
    parar = threading.Event()
    if not args.demo:
        threading.Thread(
            target=ouvir_para_sempre, args=(args.url, fila, parar), daemon=True
        ).start()

    relogio = pygame.time.Clock()
    inicio = time.monotonic()
    estado = EstadoRosto(inicio)
    navegador = None if args.sem_quadro else NavegadorDaAula()
    ultima_chave = None
    try:
        while True:
            for evento in pygame.event.get():
                if evento.type == pygame.QUIT or (
                    evento.type == pygame.KEYDOWN and evento.key in (pygame.K_ESCAPE, pygame.K_q)
                ):
                    return 0
            agora = time.monotonic()
            if args.demo:
                for topico, dados in roteiro_demo(inicio, agora):
                    estado.ao_evento(topico, dados, agora)
                roteiro_demo.ultimo = agora - inicio
                if agora - inicio > 22.0:
                    inicio, roteiro_demo.ultimo = agora, -1.0
            while not fila.empty():
                topico, dados = fila.get_nowait()
                estado.ao_evento(topico, dados, agora)
                if navegador is not None:
                    navegador.ao_evento(topico, dados, agora)
            if navegador is not None:
                estava_aberto = navegador.aberto
                navegador.tique(agora)
                if estava_aberto and not navegador.aberto:
                    ultima_chave = None  # o Firefox saiu da frente: redesenha
            rosto = estado.quadro(agora)
            # SO REDESENHA O QUE MUDOU: parado, o rosto e a mesma imagem por
            # segundos - redesenhar 30x/s gastava ~18% de um nucleo a 960x540
            # (medido 12/11) para pintar o mesmo quadro.
            chave = (
                rosto.expressao, round(rosto.abertura_olhos, 2), round(rosto.olhar_x, 2),
                round(rosto.olhar_y, 2), round(rosto.abertura_boca, 2),
            )
            if chave != ultima_chave:
                desenhar(tela, rosto)
                pygame.display.flip()
                ultima_chave = chave
            relogio.tick(FPS)
    finally:
        parar.set()
        if navegador is not None:
            navegador.fechar()
        pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
