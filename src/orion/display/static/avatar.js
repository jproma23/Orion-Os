/* ============================================================
   Fofão — FOFÃO
   Mascote 3D (Three.js r128) — puro consumidor do Event Bus
   (Cap 13 s.2): nenhuma decisao acontece aqui, so reflete os
   eventos que o avatar_server.py repassa via SSE.

   Diferenca do modelo original do usuario: em vez de rastrear
   movimento pela webcam, a cabeca segue o pan/tilt REAL dos
   servos do robo (evento motion.pan_tilt), e o estado de animo
   vem do estado de voz real (evento voice.status), nao de uma
   deteccao de movimento simulada.
   ============================================================ */

// ---------- Cena basica ----------
const container = document.getElementById('cena');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 100);
camera.position.set(0, 0.2, 6);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
container.appendChild(renderer.domElement);

// ---------- Luzes ----------
scene.add(new THREE.AmbientLight(0x8899ff, 0.5));
const keyLight = new THREE.DirectionalLight(0xffffff, 0.9);
keyLight.position.set(3, 4, 5);
scene.add(keyLight);
const rimLight = new THREE.DirectionalLight(0x4fd8eb, 0.6);
rimLight.position.set(-4, 1, -3);
scene.add(rimLight);
const eyeGlow = new THREE.PointLight(0xffb340, 0.8, 6);
eyeGlow.position.set(0, 0.3, 1.5);
scene.add(eyeGlow);

// ---------- Estrelas de fundo (ceu do sitio) ----------
(function estrelas() {
  const g = new THREE.BufferGeometry();
  const n = 350, pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = (Math.random() - 0.5) * 40;
    pos[i * 3 + 1] = (Math.random() - 0.3) * 25;
    pos[i * 3 + 2] = -8 - Math.random() * 20;
  }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const m = new THREE.PointsMaterial({ color: 0xaaccff, size: 0.06, transparent: true, opacity: 0.7 });
  scene.add(new THREE.Points(g, m));
})();

// ---------- Materiais ----------
// matOlho/matAntena mudam de cor em ALERT (definirAlerta) - por isso
// ficam em variaveis, nao inline.
const CORES = { ambar: 0xffb340, ciano: 0x4fd8eb, alerta: 0xe85d4a };

const matCasco = new THREE.MeshStandardMaterial({ color: 0xe8ecf5, metalness: 0.25, roughness: 0.35 });
const matEscuro = new THREE.MeshStandardMaterial({ color: 0x121830, metalness: 0.4, roughness: 0.5 });
const matVisor = new THREE.MeshStandardMaterial({ color: 0x0a0f24, metalness: 0.6, roughness: 0.25 });
const matOlho = new THREE.MeshBasicMaterial({ color: CORES.ambar });
const matCiano = new THREE.MeshBasicMaterial({ color: CORES.ciano });

// ---------- Robo ----------
const robo = new THREE.Group();
const cabeca = new THREE.Group();
robo.add(cabeca);
scene.add(robo);

const head = new THREE.Mesh(new THREE.SphereGeometry(1.15, 48, 48), matCasco);
head.scale.set(1, 0.88, 0.92);
cabeca.add(head);

const visor = new THREE.Mesh(new THREE.SphereGeometry(1.02, 48, 48, 0, Math.PI), matVisor);
visor.scale.set(0.98, 0.82, 0.9);
visor.rotation.y = -Math.PI / 2;
visor.position.z = 0.22;
cabeca.add(visor);

function criarOlho(x) {
  const g = new THREE.Group();
  const olho = new THREE.Mesh(new THREE.CircleGeometry(0.17, 32), matOlho);
  const brilho = new THREE.Mesh(new THREE.CircleGeometry(0.05, 16), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  brilho.position.set(0.06, 0.06, 0.01);
  g.add(olho, brilho);
  g.position.set(x, 0.18, 1.08);
  cabeca.add(g);
  return g;
}
const olhoE = criarOlho(-0.38);
const olhoD = criarOlho(0.38);

const boca = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.035, 12, 24, Math.PI), matOlho);
boca.rotation.z = Math.PI;
boca.position.set(0, -0.18, 1.06);
cabeca.add(boca);

[-0.62, 0.62].forEach(x => {
  const b = new THREE.Mesh(new THREE.CircleGeometry(0.09, 24),
    new THREE.MeshBasicMaterial({ color: 0xff8c6b, transparent: true, opacity: 0.55 }));
  b.position.set(x, -0.12, 1.0);
  b.lookAt(0, -0.12, 5);
  cabeca.add(b);
});

const haste = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.45, 12), matEscuro);
haste.position.set(0, 1.2, 0);
cabeca.add(haste);
const antena = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 20), matCiano);
antena.position.set(0, 1.46, 0);
cabeca.add(antena);
const antenaLuz = new THREE.PointLight(CORES.ciano, 0.6, 3);
antenaLuz.position.copy(antena.position);
cabeca.add(antenaLuz);

[-1.12, 1.12].forEach(x => {
  const o = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.22, 24), matEscuro);
  o.rotation.z = Math.PI / 2;
  o.position.set(x, 0.05, 0);
  cabeca.add(o);
  const anel = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.025, 10, 24), matCiano);
  anel.rotation.y = Math.PI / 2;
  anel.position.set(x + (x > 0 ? 0.115 : -0.115), 0.05, 0);
  cabeca.add(anel);
});

const corpo = new THREE.Mesh(new THREE.SphereGeometry(0.55, 32, 32), matCasco);
corpo.scale.set(1, 0.75, 0.8);
corpo.position.set(0, -1.35, 0);
robo.add(corpo);
const peito = new THREE.Mesh(new THREE.CircleGeometry(0.14, 24), matCiano);
peito.position.set(0, -1.25, 0.44);
robo.add(peito);

const anelProp = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.05, 12, 32),
  new THREE.MeshBasicMaterial({ color: CORES.ciano, transparent: true, opacity: 0.5 }));
anelProp.rotation.x = Math.PI / 2;
anelProp.position.set(0, -1.85, 0);
robo.add(anelProp);

// ---------- Estado ----------
const alvo = { x: 0, y: 0 };   // para onde a cabeca olha, vem de motion.pan_tilt
let energia = 0;               // 0 = calmo, 1 = animado - sobe com LISTENING/THINKING/SPEAKING
let alertaAtivo = false;
let falando = false;
let blinkT = 0, nextBlink = 2000;

// Limites reais dos servos (config/orion.yaml, secao vision) - buscados
// do servidor em vez de fixos aqui, para nao violar a regra de "nenhum
// valor fixo no codigo" (Cap 17). Ate a resposta chegar, usa um chute
// conservador para nao dividir por zero.
let limitePan = 80;
let limiteTilt = 45;
fetch('/config')
  .then(r => r.json())
  .then(cfg => {
    limitePan = Math.max(...cfg.pan_limits_degrees.map(Math.abs));
    limiteTilt = Math.max(...cfg.tilt_limits_degrees.map(Math.abs));
  })
  .catch(() => { /* mantem o chute conservador se o servidor nao responder */ });

// Sem modo SLEEP: a pedido do usuario, o display fica sempre ligado,
// sem escurecer por inatividade (desvio deliberado do Cap 13 s.3).

// ---------- HUD / fala ----------
const statusEl = document.getElementById('status');
const hudConn = document.getElementById('hud-conn');
const fala = document.getElementById('fala');

let falaTimer = null;
function dizer(txt, ms = 3500) {
  fala.textContent = txt;
  fala.classList.add('show');
  fala.classList.remove('legenda');  // frase curta usa o balao pequeno
  clearTimeout(falaTimer);
  falaTimer = setTimeout(() => fala.classList.remove('show'), ms);
}

// Legenda do que o Fofao esta FALANDO agora (voice.response_started).
//
// Diferente de dizer(): NAO some sozinha depois de um tempo fixo. Uma
// resposta longa leva mais que os 3,5s do balao para ser falada pelo Piper,
// e a legenda sumindo no meio da fala e pior que nao ter legenda. Quem a
// encerra e o proprio fim da fala (voice.response_finished).
function legendar(txt) {
  if (!txt) return;
  clearTimeout(falaTimer);
  fala.textContent = txt;
  fala.classList.add('show', 'legenda');
}

// Folga apos o fim da fala: o audio acabou, mas quem esta lendo ainda
// precisa de um instante para terminar a ultima linha.
function encerrarLegenda(ms = 2500) {
  clearTimeout(falaTimer);
  falaTimer = setTimeout(() => fala.classList.remove('show', 'legenda'), ms);
}

function definirAlerta(ativo) {
  alertaAtivo = ativo;
  const cor = ativo ? CORES.alerta : CORES.ambar;
  matOlho.color.setHex(cor);
  statusEl.classList.toggle('alerta', ativo);
}

// clique no robo = so uma reacao de carinho, nao afeta o estado real
renderer.domElement.addEventListener('pointerdown', () => {
  energia = Math.min(1, energia + 0.4);
  dizer('Hehe, isso faz cocegas! 🤭');
});

// ---------- conexao SSE (Cap 13 s.7) ----------
function conectar() {
  const fonte = new EventSource('/eventos');

  fonte.addEventListener('open', () => {
    statusEl.classList.add('on');
    hudConn.textContent = 'sistemas prontos';
  });
  fonte.addEventListener('error', () => {
    statusEl.classList.remove('on');
    hudConn.textContent = 'sem conexao';
    fonte.close();
    setTimeout(conectar, 2000);
  });

  fonte.addEventListener('voice.status', (evento) => {
    const dados = JSON.parse(evento.data);
    aplicarEstadoVoz(dados.estado);
  });

  // Legenda: aparece com o inicio da fala e sai um pouco depois do fim.
  fonte.addEventListener('voice.response_started', (evento) => {
    const dados = JSON.parse(evento.data);
    legendar(dados.texto);
  });

  fonte.addEventListener('voice.response_finished', () => {
    encerrarLegenda();
  });

  fonte.addEventListener('motion.pan_tilt', (evento) => {
    const dados = JSON.parse(evento.data);
    const pan = dados.pan ?? 0, tilt = dados.tilt ?? 0;
    // normaliza graus reais para -1..1, faixa que o resto da animacao usa
    alvo.x = Math.max(-1, Math.min(1, pan / limitePan));
    alvo.y = Math.max(-1, Math.min(1, -tilt / limiteTilt));
  });

  fonte.addEventListener('motion.obstacle_front', () => {
    definirAlerta(true);
    dizer('Opa, tem algo na frente! 🛑');
  });

  fonte.addEventListener('diagnostic.error', () => {
    definirAlerta(true);
    dizer('Hmm, deu um erro em algum sistema.');
  });

  fonte.addEventListener('system.ready', () => {
    definirAlerta(false);
    dizer('Sistemas prontos! Oi, eu sou o Fofão 🤖');
  });

  // O QUADRO (2026-08-07). Vocabulario FECHADO: o que nao esta em
  // `renderizadores` nao desenha nada. Mesma trava do catalogo de acoes
  // faladas - tipo desconhecido nao vira chute, vira nada.
  fonte.addEventListener('display.quadro', (evento) => {
    let dados;
    try {
      dados = JSON.parse(evento.data);
    } catch (e) {
      return;                         // evento malformado nao pode sujar a aula
    }
    escreverNoQuadro(dados);
  });
}

function aplicarEstadoVoz(estado) {
  hudConn.textContent = {
    IDLE: 'em vigilia',
    LISTENING: 'ouvindo...',
    WAKE_DETECTED: 'te ouvi!',
    TRANSCRIBING: 'entendendo...',
    THINKING: 'pensando...',
    SPEAKING: 'falando',
    ERROR: 'erro',
  }[estado] ?? estado;

  statusEl.classList.toggle('ativo', estado !== 'IDLE' && estado !== 'ERROR');

  if (estado === 'ERROR') {
    definirAlerta(true);
  } else if (alertaAtivo) {
    definirAlerta(false);
  }

  if (estado === 'WAKE_DETECTED') dizer('Oi! Pode falar!');

  energia = { IDLE: 0, LISTENING: 0.5, WAKE_DETECTED: 0.7, TRANSCRIBING: 0.5, THINKING: 0.8, SPEAKING: 0.9, ERROR: 0.3 }[estado] ?? 0;
  falando = estado === 'SPEAKING';
}

conectar();

// ---------- Animacao ----------
const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const t = clock.getElapsedTime();

  // decaimento suave de energia entre eventos, pra nao ficar "travado"
  // num pico logo apos um estado passar
  energia *= 0.99;

  // flutuacao
  robo.position.y = Math.sin(t * 1.4) * 0.12 + Math.sin(t * 0.6) * 0.05;
  robo.rotation.z = Math.sin(t * 0.8) * 0.02;
  anelProp.rotation.z = t * 2;
  anelProp.material.opacity = 0.35 + Math.sin(t * 6) * 0.15 + energia * 0.2;

  // cabeca segue o pan/tilt real do robo (suavizado)
  cabeca.rotation.y += (alvo.x * 0.55 - cabeca.rotation.y) * 0.08;
  cabeca.rotation.x += (alvo.y * 0.35 - cabeca.rotation.x) * 0.08;

  // olhos acompanham um pouco mais que a cabeca
  const ex = alvo.x * 0.12, ey = 0.18 + alvo.y * 0.08;
  olhoE.position.x += (-0.38 + ex - olhoE.position.x) * 0.15;
  olhoD.position.x += (0.38 + ex - olhoD.position.x) * 0.15;
  olhoE.position.y += (ey - olhoE.position.y) * 0.15;
  olhoD.position.y += (ey - olhoD.position.y) * 0.15;

  // piscar
  blinkT += clock.getDelta() * 1000 + 16;
  let eyeScaleY = 1;
  if (blinkT > nextBlink) {
    const p = (blinkT - nextBlink) / 180;
    if (p >= 1) { blinkT = 0; nextBlink = 1800 + Math.random() * 3000; }
    else eyeScaleY = Math.abs(Math.cos(p * Math.PI));
  }
  const abertura = 1 + energia * 0.25;
  olhoE.scale.set(1 + energia * 0.15, eyeScaleY * abertura, 1);
  olhoD.scale.set(1 + energia * 0.15, eyeScaleY * abertura, 1);

  // boca: mexe de verdade enquanto SPEAKING (simula fala), senao so
  // cresce um pouco com a energia geral
  if (falando) {
    boca.scale.set(1 + energia * 0.6, 0.6 + Math.abs(Math.sin(t * 14)) * 1.1, 1);
  } else {
    boca.scale.set(1 + energia * 0.4, 1 + energia * 0.5, 1);
  }

  // antena pulsa mais rapido com mais energia / em alerta
  const velocidadePulso = alertaAtivo ? 10 : 4;
  const pulso = 0.8 + Math.sin(t * velocidadePulso) * 0.2 + energia * 1.2;
  antenaLuz.intensity = pulso * 0.6;
  antenaLuz.color.setHex(alertaAtivo ? CORES.alerta : CORES.ciano);
  antena.scale.setScalar(1 + Math.sin(t * velocidadePulso) * 0.06 + energia * 0.25);

  renderer.render(scene, camera);
}
animate();

// ---------- Responsivo ----------
// Le o TAMANHO DA CAIXA e nao a janela: em modo aula o avatar encolhe para
// o canto (body.aula #cena, no CSS) e usar innerWidth deixaria um canvas de
// tela cheia dentro de uma caixa pequena - o robo apareceria cortado.
function ajustarCena() {
  const largura = container.clientWidth || innerWidth;
  const altura = container.clientHeight || innerHeight;
  camera.aspect = largura / altura;
  camera.updateProjectionMatrix();
  renderer.setSize(largura, altura);
}
addEventListener('resize', ajustarCena);


/* ============================================================
   O QUADRO — a tela como lousa do professor (2026-08-07)

   Regras que este trecho aplica, e todas vieram de decisao anterior:

   1. VOCABULARIO FECHADO. `renderizadores` e a lista inteira do que a
      tela sabe desenhar. Tipo que nao esta ali nao desenha nada - e
      proposital, e a mesma trava que impede a IA de inventar uma acao.
   2. NADA DE innerHTML COM TEXTO DE FORA. Todo conteudo entra por
      `textContent` ou por `createElement`. A unica excecao e o MathML,
      que passa por um parser e so e aceito se a raiz for <math> - e
      MathML nativo do Firefox, sem biblioteca nenhuma.
   3. O DEDO. A tela nao e touch e nao ha mouse: quem aponta e o robo.
      Cada bloco novo puxa o dedo, como um professor que aponta o que
      acabou de escrever.
   ============================================================ */

const quadro = document.getElementById('quadro');
const dedo = document.getElementById('dedo');

function novoBloco(id, classe) {
  const div = document.createElement('div');
  div.className = 'bloco' + (classe ? ' ' + classe : '');
  if (id) div.id = id;
  return div;
}

// Formula em texto comum: ^ eleva, _ abaixa. Feito com createElement e
// textContent - o conteudo nunca vira HTML, entao nao ha o que injetar.
function montarFormula(texto) {
  const raiz = document.createDocumentFragment();
  let atual = '';
  let modo = null;                    // null | 'sup' | 'sub'
  const despejar = () => {
    if (!atual) return;
    if (modo) {
      const marca = document.createElement(modo);
      marca.textContent = atual;
      raiz.appendChild(marca);
    } else {
      raiz.appendChild(document.createTextNode(atual));
    }
    atual = '';
  };
  for (const caractere of texto) {
    if (caractere === '^' || caractere === '_') {
      despejar();
      modo = caractere === '^' ? 'sup' : 'sub';
    } else if (modo && !/[0-9a-zA-Z+\-]/.test(caractere)) {
      // o expoente acaba no primeiro caractere que nao pode fazer parte
      // dele: "x^2 + 1" eleva o 2 e devolve o resto para a linha de base
      despejar();
      modo = null;
      atual = caractere;
    } else {
      atual += caractere;
    }
  }
  despejar();
  return raiz;
}

const renderizadores = {
  titulo(dados, bloco) {
    const h = document.createElement('h2');
    h.textContent = dados.texto;
    bloco.appendChild(h);
  },

  texto(dados, bloco) {
    const p = document.createElement('p');
    p.textContent = dados.texto;
    bloco.appendChild(p);
  },

  topicos(dados, bloco) {
    if (dados.texto) {
      const rotulo = document.createElement('div');
      rotulo.className = 'rotulo-lista';
      rotulo.textContent = dados.texto;
      bloco.appendChild(rotulo);
    }
    const ul = document.createElement('ul');
    (dados.itens || []).forEach(item => {
      const li = document.createElement('li');
      li.textContent = item;
      ul.appendChild(li);
    });
    bloco.appendChild(ul);
  },

  formula(dados, bloco) {
    bloco.classList.add('formula');
    bloco.appendChild(montarFormula(dados.texto));
  },

  mathml(dados, bloco) {
    bloco.classList.add('mathml');
    // Parser de verdade em vez de innerHTML: so entra na pagina se a raiz
    // for <math>. Se o modelo escrever algo torto, cai para a formula em
    // texto - a aula continua, que e o que importa.
    try {
      const doc = new DOMParser().parseFromString(dados.texto, 'application/xml');
      const raiz = doc.documentElement;
      if (raiz && raiz.localName === 'math' && !doc.querySelector('parsererror')) {
        bloco.appendChild(document.importNode(raiz, true));
        return;
      }
    } catch (e) { /* cai no plano B logo abaixo */ }
    bloco.classList.remove('mathml');
    bloco.classList.add('formula');
    bloco.appendChild(montarFormula(dados.texto));
  },

  codigo(dados, bloco) {
    if (dados.linguagem) {
      const etiqueta = document.createElement('span');
      etiqueta.className = 'linguagem';
      etiqueta.textContent = dados.linguagem;
      bloco.appendChild(etiqueta);
    }
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = dados.texto;   // a indentacao E o conteudo, aqui
    pre.appendChild(code);
    bloco.appendChild(pre);
  },

  tabela(dados, bloco) {
    if (dados.texto) {
      const rotulo = document.createElement('div');
      rotulo.className = 'rotulo-lista';
      rotulo.textContent = dados.texto;
      bloco.appendChild(rotulo);
    }
    const tabela = document.createElement('table');
    if (dados.cabecalho) {
      const tr = document.createElement('tr');
      dados.cabecalho.forEach(titulo => {
        const th = document.createElement('th');
        th.textContent = titulo;
        tr.appendChild(th);
      });
      tabela.appendChild(tr);
    }
    (dados.linhas || []).forEach(linha => {
      const tr = document.createElement('tr');
      (Array.isArray(linha) ? linha : [linha]).forEach(celula => {
        const td = document.createElement('td');
        td.textContent = celula;
        tr.appendChild(td);
      });
      tabela.appendChild(tr);
    });
    bloco.appendChild(tabela);
  },

  // SVG gerado aqui mesmo, sem biblioteca de grafico: sao barras, e barras
  // sao quatro numeros por retangulo. Uma dependencia nova precisaria de um
  // motivo maior que este.
  grafico(dados, bloco) {
    const valores = (dados.valores || []).map(Number).filter(v => !isNaN(v));
    if (!valores.length) return;
    if (dados.texto) {
      const rotulo = document.createElement('div');
      rotulo.className = 'rotulo-lista';
      rotulo.textContent = dados.texto;
      bloco.appendChild(rotulo);
    }
    const NS = 'http://www.w3.org/2000/svg';
    const largura = 1000, altura = 420, base = 360, folga = 60;
    const maximo = Math.max(...valores) || 1;
    const passo = (largura - folga * 2) / valores.length;
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${largura} ${altura}`);

    const eixo = document.createElementNS(NS, 'line');
    eixo.setAttribute('x1', folga - 10); eixo.setAttribute('y1', base);
    eixo.setAttribute('x2', largura - folga + 10); eixo.setAttribute('y2', base);
    eixo.setAttribute('stroke', 'rgba(255,255,255,.35)');
    svg.appendChild(eixo);

    valores.forEach((valor, i) => {
      const alturaBarra = (valor / maximo) * (base - 40);
      const x = folga + i * passo + passo * 0.18;
      const larguraBarra = passo * 0.64;
      const barra = document.createElementNS(NS, 'rect');
      barra.setAttribute('x', x);
      barra.setAttribute('y', base - alturaBarra);
      barra.setAttribute('width', larguraBarra);
      barra.setAttribute('height', alturaBarra);
      barra.setAttribute('rx', 6);
      barra.setAttribute('fill', '#4fd8eb');
      barra.setAttribute('opacity', '.85');
      svg.appendChild(barra);

      const numero = document.createElementNS(NS, 'text');
      numero.setAttribute('x', x + larguraBarra / 2);
      numero.setAttribute('y', base - alturaBarra - 12);
      numero.setAttribute('text-anchor', 'middle');
      numero.setAttribute('fill', '#eef2ff');
      numero.setAttribute('font-size', '26');
      numero.textContent = valor;
      svg.appendChild(numero);

      const nome = (dados.rotulos || [])[i];
      if (nome) {
        const legenda = document.createElementNS(NS, 'text');
        legenda.setAttribute('x', x + larguraBarra / 2);
        legenda.setAttribute('y', base + 34);
        legenda.setAttribute('text-anchor', 'middle');
        legenda.setAttribute('fill', 'rgba(238,242,255,.75)');
        legenda.setAttribute('font-size', '24');
        legenda.textContent = nome;
        svg.appendChild(legenda);
      }
    });
    bloco.appendChild(svg);
  },
};

// Move o dedo para a esquerda de um bloco. Coordenadas de tela (o dedo e
// `position: fixed`), entao a rolagem do quadro nao o deixa para tras.
function apontarPara(elemento) {
  if (!elemento) return;
  const caixa = elemento.getBoundingClientRect();
  const x = Math.max(6, caixa.left - 54);
  const y = caixa.top + Math.min(caixa.height / 2, 40) - 22;
  dedo.style.transform = `translate(${x}px, ${y}px)`;
  dedo.classList.add('mostrar');
  document.querySelectorAll('.bloco.destaque').forEach(b => b.classList.remove('destaque'));
  elemento.classList.add('destaque');
}

function escreverNoQuadro(dados) {
  if (dados.tipo === 'limpar') {
    quadro.replaceChildren();
    document.body.classList.remove('aula');
    dedo.classList.remove('mostrar');
    ajustarCena();
    return;
  }

  if (dados.tipo === 'apontar') {
    apontarPara(document.getElementById(dados.alvo));
    return;
  }

  const desenhar = renderizadores[dados.tipo];
  if (!desenhar) return;              // vocabulario fechado: nao inventa

  const primeiraVez = !document.body.classList.contains('aula');
  document.body.classList.add('aula');
  if (primeiraVez) ajustarCena();     // o avatar acabou de encolher

  const bloco = novoBloco(dados.id, null);
  desenhar(dados, bloco);
  quadro.appendChild(bloco);
  bloco.scrollIntoView({ behavior: 'smooth', block: 'end' });
  // O dedo segue o que acabou de ser escrito. `requestAnimationFrame` para
  // medir DEPOIS do layout - antes disso o bloco novo ainda nao tem posicao.
  requestAnimationFrame(() => apontarPara(bloco));
}


// -------------------------------------------------- marca d'agua da build
//
// Os dados vem de /config, que os le do git na subida do servidor. Nada
// aqui e escrito a mao: data fixa no JS mente no dia seguinte, e a marca
// existe justamente para dizer QUANDO esta versao foi feita.
(async function marcaDagua() {
  const alvo = document.getElementById("marca");
  if (!alvo) return;
  let build;
  try {
    const resposta = await fetch("/config", { cache: "no-store" });
    build = (await resposta.json()).build;
  } catch (e) {
    return; // sem rede para o proprio servidor: a marca simplesmente nao aparece
  }
  if (!build) return;

  const linha = (rotulo, valor) =>
    valor ? `<div class="linha"><span class="rotulo">${rotulo}</span> ${valor}</div>` : "";

  alvo.innerHTML = [
    `<div class="produto">${build.software} v${build.versao}</div>`,
    linha("dev", build.autor),
    linha("criado", build.criado_em),
    linha("atualizado", build.atualizado_em),
    linha("build", build.commit),
  ].join("");
})();
