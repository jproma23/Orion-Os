// ORION X — Diagnóstico (Cap 13 s.4-5): saúde dos módulos, últimos erros
// e acesso ao log (somente leitura). Consumidor puro via SSE.

const $ = (id) => document.getElementById(id);
const conexaoEl = $('conexao');
const listaModulos = $('lista-modulos');
const listaErros = $('lista-erros');

function hora(timestampS) {
  return new Date(timestampS * 1000).toLocaleTimeString('pt-BR');
}

function renderizarModulos(modulos) {
  const nomes = Object.keys(modulos);
  if (nomes.length === 0) {
    listaModulos.innerHTML = '<li class="vazio">nenhum evento de saúde ainda</li>';
    return;
  }
  listaModulos.innerHTML = '';
  for (const nome of nomes) {
    const info = modulos[nome];
    const item = document.createElement('li');
    const classe = info.status === 'ok' ? 'status-ok' : 'status-perdido';
    const texto = info.status === 'ok' ? 'ok' : 'PERDIDO';
    item.innerHTML = `<span>${nome}</span><span class="${classe}">${texto} — ${hora(info.timestamp)}</span>`;
    listaModulos.appendChild(item);
  }
}

function adicionarErro(topico, dados, timestampS) {
  if (listaErros.querySelector('.vazio')) listaErros.innerHTML = '';
  const item = document.createElement('li');
  item.innerHTML = `<span>${hora(timestampS)} · ${topico}</span><span></span>`;
  item.querySelector('span:last-child').textContent = JSON.stringify(dados);
  listaErros.prepend(item);
  while (listaErros.children.length > 20) listaErros.removeChild(listaErros.lastChild);
}

async function carregarEstadoInicial() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
  renderizarModulos(corpo.estado.diagnostico.modulos);
  const erros = corpo.estado.diagnostico.ultimos_erros;
  if (erros.length === 0) {
    listaErros.innerHTML = '<li class="vazio">nenhum erro registrado</li>';
  } else {
    erros.forEach((erro) => adicionarErro(erro.topico, erro.dados, erro.timestamp));
  }
}

async function carregarLog() {
  const resposta = await fetch(`/log?linhas=${$('log-contagem').textContent}`);
  const corpo = await resposta.json();
  $('log-conteudo').textContent = corpo.aviso || corpo.linhas.join('');
}

function conectar() {
  const fonte = new EventSource('/eventos');

  fonte.addEventListener('open', () => {
    conexaoEl.textContent = 'conectado';
    conexaoEl.className = 'badge badge--on';
  });
  fonte.addEventListener('error', () => {
    conexaoEl.textContent = 'sem conexão';
    conexaoEl.className = 'badge badge--off';
    fonte.close();
    setTimeout(conectar, 2000);
  });

  for (const topico of ['diagnostic.error', 'comm.link_degraded']) {
    fonte.addEventListener(topico, (evento) => {
      adicionarErro(topico, JSON.parse(evento.data), Date.now() / 1000);
    });
  }
  for (const topico of ['comm.module_lost', 'comm.module_recovered']) {
    fonte.addEventListener(topico, () => {
      fetch('/estado')
        .then((r) => r.json())
        .then((corpo) => renderizarModulos(corpo.estado.diagnostico.modulos));
    });
  }
}

carregarEstadoInicial();
carregarLog();
setInterval(carregarLog, 10000); // log e arquivo, nao vem por evento - reconsulta periodica
conectar();
