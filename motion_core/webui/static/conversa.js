// Fofão — Conversa (Cap 13 s.4: "transcrição da interação por voz").
//
// Histórico vem do banco (Fase 3, /api/conversas) - eventos novos só avisam que
// precisa recarregar (memory.updated), o dado de verdade sempre vem da API,
// nunca fica duplicado em dois lugares.
//
// Desde 2026-08-03 esta página também MANDA: a caixa de texto entra no mesmo
// `processar_comando` que uma transcrição do microfone entraria. Por isso o
// turno digitado é gravado em `conversas` pelo mission_planner igual ao falado,
// e é por isso que recarregar da API depois de enviar mostra os dois lados sem
// nenhuma lógica de espelho aqui.
//
// A câmera e o joystick da cabeça vêm de camera_cabeca.js, o mesmo arquivo que
// o dashboard usa.

const $ = (id) => document.getElementById(id);
const conexaoEl = $('conexao');
const lista = $('lista-conversa');
const avisoEl = $('conversa-aviso');
const forma = $('chat-forma');
const campo = $('chat-texto');
const botao = $('chat-enviar');
const chatStatus = $('chat-status');

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
    // textContent, nunca innerHTML: o texto vem do banco, e banco não é lugar
    // de confiar em marcação. Uma transcrição com "<" quebraria a lista.
    li.textContent = item.texto;
    const marca = document.createElement('span');
    marca.className = 'hora';
    marca.textContent = hora(item.timestamp);
    li.appendChild(marca);
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

// Um balão provisório enquanto o robô pensa. Não é o histórico: some assim que
// a API responde e o de verdade chega no lugar. Existe porque a resposta pode
// levar segundos (modelo + fala) e uma tela parada parece uma tela quebrada.
function balaoProvisorio(texto) {
  const vazio = lista.querySelector('.vazio');
  if (vazio) vazio.remove();
  const li = document.createElement('li');
  li.className = 'balao balao--usuario balao--enviando';
  li.textContent = texto;
  lista.appendChild(li);
  lista.scrollTop = lista.scrollHeight;
}

async function enviarMensagem(texto) {
  campo.value = '';
  campo.disabled = true;
  botao.disabled = true;
  chatStatus.className = 'chat-status';
  chatStatus.textContent = 'o Fofão está pensando...';
  balaoProvisorio(texto);
  try {
    const resposta = await fetch('/api/conversa/mensagem', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto }),
    });
    const corpo = await resposta.json().catch(() => ({}));
    if (!resposta.ok) {
      chatStatus.className = 'chat-status erro';
      chatStatus.textContent = corpo.erro || `recusado (HTTP ${resposta.status})`;
      // devolve o texto para não perder o que a pessoa escreveu
      campo.value = texto;
    } else {
      chatStatus.textContent = '';
    }
  } catch (e) {
    chatStatus.className = 'chat-status erro';
    chatStatus.textContent = 'sem resposta do robô';
    campo.value = texto;
  } finally {
    campo.disabled = false;
    botao.disabled = false;
    campo.focus();
    // Recarrega SEMPRE, inclusive no erro: o pedido pode ter chegado e a
    // resposta ter se perdido no caminho de volta. O banco decide o que houve,
    // não esta página.
    await carregarConversas();
  }
}

forma.addEventListener('submit', (evento) => {
  evento.preventDefault();
  const texto = campo.value.trim();
  if (texto) enviarMensagem(texto);
});

async function carregarEstado() {
  const resposta = await fetch('/estado');
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.estado.sistema.robot_name
    ? `— ${corpo.estado.sistema.robot_name}`
    : '';
  montarCamera(corpo.estado.camera);
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

  // A câmera só aparece quando o Motion Core informa a porta; sem isto a
  // página carregada antes do system.ready ficava sem câmera até o F5.
  fonte.addEventListener('system.ready', () => carregarEstado());
}

carregarEstado();
carregarConversas();
conectar();
