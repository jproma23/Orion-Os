# ORION OS — Jornal de Recuperação

Log cronológico do desenvolvimento. Cada entrada é escrita **no momento em
que a etapa acontece**, não em resumo de fim de sessão. Objetivo: qualquer
sessão nova (minha ou do Claude) consegue ler este arquivo e saber
exatamente onde o projeto parou, o que já funciona e o que falta.

Formato de cada entrada: data, o que foi feito, estado resultante, próximo
passo.

---

## 2026-07-16

- Recebido o scaffold inicial do projeto (`orion-os-tcc.zip`) via e-mail,
  transferido do celular para o Raspberry Pi por servidor de upload
  temporário (encerrado após uso) e extraído em `~/orion-os`.
- Repositório git inicializado em `~/orion-os` (ainda sem commits).
- Decisão: desenvolvimento vai seguir `PLANO_IMPLEMENTACAO.md` fase por
  fase, sem pular etapas, sempre com os testes da fase passando antes de
  avançar (regra já definida no próprio plano).
- Estado do código: apenas scaffold. Único código funcional é
  `src/orion/__main__.py` (Fase 0, imprime versão) e o teste de fumaça
  `tests/unit/test_smoke.py`. Nenhuma fase concluída ainda.
- **Fase 0 concluída.** Criado `.venv`, `pip install -e ".[dev]"` sem erros,
  `tools/check.sh` passa limpo (ruff OK, 1 teste de fumaça OK).
  `python -m orion` e `python -m orion --sim` funcionam (só imprimem versão,
  boot real ainda não existe — é da Fase 1).
- **Próximo passo:** iniciar Fase 1 — Kernel (Cap 6): Configuration Manager
  lendo `config/orion.yaml`, Logger estruturado, Event Bus assíncrono,
  Service Registry, Health Monitor + Watchdog, Boot Manager. Ler
  `docs/ses/ORION_OS_SES_Capitulo_06_Kernel_ORION_OS.md` antes de começar.

