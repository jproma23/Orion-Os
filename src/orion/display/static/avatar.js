/* ============================================================
   ORION X — FOFÃO
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
  clearTimeout(falaTimer);
  falaTimer = setTimeout(() => fala.classList.remove('show'), ms);
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
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
