// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// Small DOM toolkit. h('div.card', {onclick}, child, ...) builds an element; strings become text.

export function h(tag, props, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (props && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'class') el.className += (el.className ? ' ' : '') + value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key in el && typeof value !== 'string') el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const SVG = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  for (const child of children.flat()) if (child) el.append(child);
  return el;
}

// 20 px line icons on a 24 grid, stroked with currentColor.
const ICONS = {
  live: 'M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18Zm-9 9h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z',
  apps: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  controls: 'M9 3h6v6h6v6h-6v6H9v-6H3V9h6z',
  device: 'M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M12 7a5 5 0 1 0 0 10a5 5 0 0 0 0-10Z',
  link: 'M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1',
  unlink: 'M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66M4 4l2 2M18 18l2 2',
  reset: 'M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  plus: 'M12 5v14M5 12h14',
  close: 'M6 6l12 12M18 6L6 18',
  up: 'M12 19V5M6 11l6-6l6 6',
  down: 'M12 5v14M6 13l6 6l6-6',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  record: 'M12 7a5 5 0 1 0 0 10a5 5 0 0 0 0-10Z',
  sun: 'M12 8a4 4 0 1 0 0 8a4 4 0 0 0 0-8ZM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z',
  info: 'M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18ZM12 11v5M12 7.5v.5',
  warn: 'M12 4l9 16H3zM12 10v4M12 17v.5',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4',
  target: 'M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18ZM12 8a4 4 0 1 0 0 8a4 4 0 0 0 0-8ZM12 11.5v1',
  bolt: 'M13 3L5 13h6l-1 8l8-10h-6z',
  usb: 'M12 3v15M8 7l4-4l4 4M7 12v3l5 3M17 10v3l-5 3M12 18a2 2 0 1 0 0 4a2 2 0 0 0 0-4Z',
  ble: 'M7 7l10 10l-5 4V3l5 4L7 17',
  chevron: 'M9 6l6 6l-6 6',
  code: 'M9 7l-5 5l5 5M15 7l5 5l-5 5',
};

export function icon(name, size = 18) {
  return svg('svg', {viewBox: '0 0 24 24', width: size, height: size, class: 'icon',
                     'aria-hidden': 'true'},
             svg('path', {d: ICONS[name] || ICONS.info}));
}

// The mark: an astrolabe's mater and rete, drawn at any size.
export function mark(size = 28) {
  return svg('svg', {viewBox: '0 0 32 32', width: size, height: size, class: 'mark',
                     'aria-hidden': 'true'},
             svg('circle', {cx: 16, cy: 16, r: 13}),
             svg('circle', {cx: 16, cy: 16, r: 7.5}),
             svg('path', {d: 'M16 3v26M5.2 9.5l21.6 13M9 16a7 7 0 0 1 14 0'}),
             svg('circle', {cx: 16, cy: 16, r: 1.6, class: 'pin'}));
}

export function debounce(fn, ms) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...args); }, ms);
  };
  wrapped.flush = (...args) => { if (timer) { clearTimeout(timer); timer = null; fn(...args); } };
  return wrapped;
}

export function formatNumber(value) {
  if (typeof value !== 'number' || !isFinite(value)) return String(value ?? '');
  return Number.isInteger(value) ? String(value) : String(+value.toFixed(4));
}

export function titleCase(text) {
  return String(text).replace(/[_.]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// A transient message in the corner. kind: 'ok' | 'error' | 'info'.
export function toast(message, kind = 'info') {
  let tray = document.querySelector('.toasts');
  if (!tray) document.body.append(tray = h('div.toasts', {role: 'status', 'aria-live': 'polite'}));
  const item = h(`div.toast.${kind}`, icon(kind === 'error' ? 'warn' : kind === 'ok' ? 'check' : 'info', 16),
                 h('span', message));
  tray.append(item);
  setTimeout(() => item.classList.add('leaving'), kind === 'error' ? 6000 : 2600);
  setTimeout(() => item.remove(), kind === 'error' ? 6400 : 3000);
}

// A modal dialog. Resolves with the value of the button pressed, or null when dismissed.
export function dialog({title, body, buttons = [{label: 'OK', value: true, primary: true}], wide}) {
  return new Promise(resolve => {
    const close = value => { backdrop.remove(); document.removeEventListener('keydown', onKey); resolve(value); };
    const onKey = event => { if (event.key === 'Escape') close(null); };
    const box = h(`div.dialog${wide ? '.wide' : ''}`, {role: 'dialog', 'aria-modal': 'true'},
      h('header', h('h2', title),
        h('button.icon-button', {onclick: () => close(null), 'aria-label': 'Close'}, icon('close'))),
      h('div.dialog-body', body),
      h('footer', buttons.map(button => h(`button.button${button.primary ? '.primary' : ''}${button.danger ? '.danger' : ''}`,
        {onclick: () => close(button.value)}, button.label))));
    const backdrop = h('div.backdrop', {onclick: event => { if (event.target === backdrop) close(null); }}, box);
    document.body.append(backdrop);
    document.addEventListener('keydown', onKey);
    (box.querySelector('button.primary') || box.querySelector('footer button'))?.focus();
  });
}

export async function copyText(text, what = 'Copied') {
  try {
    await navigator.clipboard.writeText(text);
    toast(what, 'ok');
  } catch {
    toast('Copy failed — select the text and copy it manually', 'error');
  }
}
