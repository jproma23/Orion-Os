// ORION X — Conversa (Cap 13 s.4: "transcrição da interação por voz").
// Historico vem do banco (Fase 3, /api/conversas) - eventos novos so
// avisam que precisa recarregar (memory.updated), o dado de verdade
// sempre vem da API, nunca fica duplicado em dois lugares.

const $ = (id) => document.getElementById(id);
const conexaoEl = $('conexao');
const lista = $('lista-conversa');
const avisoEl = $('conversa-aviso');

function hora(timestampIso) {
  try {
    return new Date(timestampIso).toLocaleString('pt-BR');
  } catch {
    return timestampIso;
  }
}

function renderizar(conversas) {
  if (conversas.length === 0) {
    lista.innerHTML = '<li class="vazio">nenhuma conversa registrada ainda</li>';
    return;
  }
  lista.innerHTML = '';
  for (const item of conversas) {
    const li = document.createElement('li');
    const ehUsuario = item.papel === 'usuario' || item.papel === 'user';
    li.className = `balao ${ehUsuario ? 'balao--usuario' : 'balao--robo'}`;
    li.innerHTML = `${item.texto}<span class="hora"></span>`;
    li.querySelector('.hora').textContent = hora(item.timestamp);
    lista.appendChild(li);
  }
  lista.scrollTop = lista.scrollHeight;
}

async function carregarConversas() {
  const resposta = await fetch('/api/conversas');
  const corpo = await resposta.json();
  if (corpo.aviso) {
    avisoEl.textContent = corpo.aviso;
    avisoEl.hidden = false;
  } else {
    avisoEl.hidden = true;
  }
  renderizar(corpo.conversas);
}

async function carregarNomeDoRobo() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
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

  fonte.addEventListener('memory.updated', (evento) => {
    const dados = JSON.parse(evento.data);
    if (dados.categoria === 'conversas') carregarConversas();
  });
}

carregarNomeDoRobo();
carregarConversas();
conectar();
