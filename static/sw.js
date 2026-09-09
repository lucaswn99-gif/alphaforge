/* Service worker do AlphaForge.
 *
 * Estratégia deliberada, e ela importa num terminal financeiro:
 *
 *   - CASCA (HTML, ícones, manifest): cache primeiro, rede em segundo plano.
 *     É o que faz o app abrir instantâneo em vez de esperar o Render acordar.
 *   - DADOS (qualquer /api/, /renda-*, /wealth/): SOMENTE REDE, nunca cache.
 *     Preço e múltiplo servidos de cache viram número velho sem aviso — que é
 *     exatamente o erro que este projeto passou meses consertando. Sem rede, a
 *     tela mostra o erro; ela não inventa um valor de ontem.
 */
const VERSAO = 'alphaforge-v1';
const CASCA = [
  '/',
  '/static/manifest.webmanifest',
  '/static/icone-192.png',
  '/static/icone-512.png',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((chaves) => Promise.all(chaves.filter((k) => k !== VERSAO).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Endpoints de dado: nunca entram em cache.
const E_DADO = (url) => /\/(api|renda-fixa|renda-variavel|wealth|health)\b/.test(url.pathname);

self.addEventListener('fetch', (evento) => {
  const req = evento.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // logos e CDNs seguem direto
  if (E_DADO(url)) return;                           // dado sempre da rede

  evento.respondWith(
    caches.match(req).then((cacheado) => {
      const daRede = fetch(req)
        .then((resposta) => {
          if (resposta && resposta.status === 200) {
            const copia = resposta.clone();
            caches.open(VERSAO).then((c) => c.put(req, copia));
          }
          return resposta;
        })
        .catch(() => cacheado);
      return cacheado || daRede;
    })
  );
});
