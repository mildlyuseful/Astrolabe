// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// The shell: talks to the daemon, routes between pages, and keeps the sidebar live.
//
// A page is a module exporting {id, title, icon, render(ctx), live?(ctx), motion?, input?}.
// To add one, write the module and list it in PAGES; nothing else needs to know.

import {h, icon, mark, toast} from './ui.js';
import live from './page-live.js';
import apps from './page-apps.js';
import controls from './page-controls.js';
import device from './page-device.js';
import welcome from './page-welcome.js';

const PAGES = [live, apps, controls, device];
const ALL_PAGES = [...PAGES, welcome];

// --- talking to the daemon -----------------------------------------------------------------

function readToken() {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get('t');
  if (token) {
    try { sessionStorage.setItem('astrolabe-token', token); } catch {}
    history.replaceState(null, '', `#/${params.get('p') || ''}`);
    return token;
  }
  try { return sessionStorage.getItem('astrolabe-token'); } catch { return null; }
}

const token = readToken();

async function getJson(path) {
  const response = await fetch(path, {headers: {'X-Astrolabe-Token': token}, cache: 'no-store'});
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

// Run a named daemon action. Resolves with its result; rejects (and toasts) with its error.
async function act(action, params = {}, {quiet = false} = {}) {
  const response = await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Astrolabe-Token': token},
    body: JSON.stringify({action, params}),
  });
  const reply = await response.json();
  if (!reply.ok && !('message' in reply)) {
    if (!quiet) toast(reply.error || 'That did not work', 'error');
    throw new Error(reply.error);
  }
  return 'result' in reply ? reply.result : reply;
}

// --- state ---------------------------------------------------------------------------------

const ctx = {
  state: null,
  live: null,
  pressed: [],
  route: {page: 'live', parts: []},
  act,
  navigate(path) { location.hash = `#/${path}`; },
  rerender: () => render(),
  // Pages keep small UI-only state here (selected tab, open editor) so it survives re-renders.
  memory: {},
};

let refreshing = null;
async function refreshState() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      ctx.state = await getJson('/api/state');
      render();
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function refreshLive() {
  ctx.live = await getJson('/api/live');
  ctx.pressed = ctx.live.pressed;
  updateSidebar();
  current()?.live?.(ctx);
}

function connectEvents() {
  const events = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  let stateTimer = null;
  events.addEventListener('open', () => { setOffline(false); refreshState(); refreshLive(); });
  events.addEventListener('state', () => {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(refreshState, 40);
  });
  events.addEventListener('live', () => refreshLive().catch(() => {}));
  events.addEventListener('motion', event => current()?.motion?.(ctx, JSON.parse(event.data)));
  events.addEventListener('input', event => {
    ctx.pressed = JSON.parse(event.data);
    current()?.input?.(ctx);
  });
  events.addEventListener('error', () => setOffline(true));
}

// --- routing and rendering -----------------------------------------------------------------

function parseRoute() {
  const [page, ...parts] = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  ctx.route = {page: ALL_PAGES.some(p => p.id === page) ? page : 'live', parts};
}

function current() {
  return ALL_PAGES.find(page => page.id === ctx.route.page);
}

const main = h('main.page', {id: 'page'});
const navLinks = new Map();
let sidebarStatus;

function buildShell() {
  const nav = h('nav.nav', PAGES.map(page => {
    const link = h('a.nav-item', {href: `#/${page.id}`}, icon(page.icon, 20), h('span', page.title));
    navLinks.set(page.id, link);
    return link;
  }));
  sidebarStatus = h('div.side-status');
  const theme = h('button.icon-button.theme', {onclick: toggleTheme, title: 'Switch theme',
                                                'aria-label': 'Switch theme'});
  const shell = h('div.shell',
    h('aside.sidebar',
      h('div.brand', mark(30), h('div', h('strong', 'Astrolabe'), h('span.version', ''))),
      nav,
      h('div.side-foot', sidebarStatus, theme)),
    main);
  document.body.replaceChildren(shell);
  applyTheme();
}

// Re-render the page without losing the user's place: the focused field (by data-key) keeps
// focus and caret, and the scroll position stays put.
function render() {
  if (!ctx.state) return;
  const page = current();
  for (const [id, link] of navLinks) link.classList.toggle('active', id === page.id);
  document.querySelector('.brand .version').textContent = `v${ctx.state.product.version}`;

  const focused = document.activeElement;
  const key = focused?.dataset?.key;
  const caret = key && 'selectionStart' in focused ? [focused.selectionStart, focused.selectionEnd] : null;
  const scrollers = [...main.querySelectorAll('[data-scroll]')].map(el => [el.dataset.scroll, el.scrollTop]);
  const top = main.scrollTop;

  let content;
  try {
    content = page.render(ctx);
  } catch (error) {
    console.error(error);
    content = h('div.empty', h('h2', 'This page failed to render'), h('pre', String(error.stack || error)));
  }
  main.replaceChildren(content);

  main.scrollTop = top;
  for (const [name, position] of scrollers) {
    const el = main.querySelector(`[data-scroll="${name}"]`);
    if (el) el.scrollTop = position;
  }
  if (key) {
    const again = main.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (again) {
      again.focus({preventScroll: true});
      if (caret && 'setSelectionRange' in again) {
        try { again.setSelectionRange(...caret); } catch {}
      }
    }
  }
  updateSidebar();
  page.live?.(ctx);
}

function updateSidebar() {
  if (!sidebarStatus || !ctx.live) return;
  const c = ctx.live.connection;
  const usb = /usb/i.test(c.status);
  sidebarStatus.replaceChildren(...[
    h('div.side-device', h(`span.dot${c.connected ? '.on' : ''}`),
      h('div', h('strong', ctx.state?.global['device.name']?.value || 'Device'),
        h('span', c.connected ? (usb ? 'USB' : 'Bluetooth') : 'Not connected'))),
    c.battery != null ? h('div.battery', {title: c.power},
      h('div.battery-bar', h('div', {style: {width: `${c.battery}%`}})), h('span', `${c.battery}%`)) : null,
  ].filter(Boolean));
}

let offlineBanner = null;
function setOffline(offline) {
  if (offline && !offlineBanner) {
    offlineBanner = h('div.offline', icon('warn', 16),
      h('span', 'Lost contact with the Astrolabe daemon. If it restarted, reopen settings from the tray.'));
    document.body.append(offlineBanner);
  } else if (!offline && offlineBanner) {
    offlineBanner.remove();
    offlineBanner = null;
  }
}

// --- theme ---------------------------------------------------------------------------------

function storedTheme() {
  try { return localStorage.getItem('astrolabe-theme'); } catch { return null; }
}

function applyTheme() {
  const theme = storedTheme();
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const dark = theme ? theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  document.querySelector('.theme')?.replaceChildren(icon(dark ? 'sun' : 'moon', 18));
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  try { localStorage.setItem('astrolabe-theme', dark ? 'light' : 'dark'); } catch {}
  applyTheme();
  current()?.live?.(ctx);
}

// --- start ---------------------------------------------------------------------------------

const reported = new Set();
window.addEventListener('error', event => {
  if (reported.has(event.message)) return;
  reported.add(event.message);
  toast(`UI error: ${event.message}`, 'error');
});
window.addEventListener('unhandledrejection', event => {
  if (event.reason && !String(event.reason.message || '').startsWith('/api')) console.error(event.reason);
});

if (!token) {
  document.body.replaceChildren(h('div.empty.fullscreen', mark(48), h('h1', 'Open Astrolabe from the tray'),
    h('p', 'This page needs the link the daemon opens for you.')));
} else {
  buildShell();
  parseRoute();
  window.addEventListener('hashchange', () => { parseRoute(); main.scrollTop = 0; render(); });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
  connectEvents();
  refreshState().then(() => {
    if (ctx.state.firstRun && !location.hash.replace(/^#\/?/, '')) ctx.navigate('welcome');
  }).catch(error => toast(String(error), 'error'));
}
