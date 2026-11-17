// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

import {h, icon, dialog, toast} from './ui.js';
import {segmented, select, toggle} from './settings.js';

const KEY_NAMES = {
  Space: 'space', Enter: 'enter', Tab: 'tab', Escape: 'escape', Backspace: 'backspace',
  Delete: 'delete', Insert: 'insert', Home: 'home', End: 'end', PageUp: 'page_up',
  PageDown: 'page_down', ArrowLeft: 'arrow.left', ArrowRight: 'arrow.right', ArrowUp: 'arrow.up',
  ArrowDown: 'arrow.down', CapsLock: 'caps_lock', NumpadEnter: 'numpad.enter',
  NumpadAdd: 'numpad.add', NumpadSubtract: 'numpad.subtract', NumpadMultiply: 'numpad.multiply',
  NumpadDivide: 'numpad.divide', NumpadDecimal: 'numpad.decimal', Pause: 'pause',
  ScrollLock: 'scroll_lock', NumLock: 'num_lock', ContextMenu: 'context_menu',
  PrintScreen: 'print_screen',
};
const KEY_LABELS = {ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', meta: 'Win', space: 'Space',
  'arrow.left': '←', 'arrow.right': '→', 'arrow.up': '↑', 'arrow.down': '↓', escape: 'Esc',
  page_up: 'PgUp', page_down: 'PgDn', enter: 'Enter', tab: 'Tab'};
const FIVEWAY = ['up', 'left', 'center', 'right', 'down'];

function keyToken(event) {
  const code = event.code;
  let name = null;
  if (code.startsWith('Control')) name = 'ctrl';
  else if (code.startsWith('Shift')) name = 'shift';
  else if (code.startsWith('Alt')) name = 'alt';
  else if (code.startsWith('Meta') || code.startsWith('OS')) name = 'meta';
  else if (/^Key[A-Z]$/.test(code)) name = code.slice(3).toLowerCase();
  else if (/^Digit\d$/.test(code)) name = code.slice(5);
  else if (/^F\d{1,2}$/.test(code)) name = code.toLowerCase();
  else if (/^Numpad\d$/.test(code)) name = `numpad.${code.slice(6)}`;
  else name = KEY_NAMES[code] || null;
  return name && `keyboard:${name}`;
}

function tokenLabel(ctx, token) {
  const control = ctx.state.bindings.controls.find(item => item.token === token);
  if (control) return control.label.replace(/^Astrolabe /, '');
  const [source, name] = token.split(':');
  if (source === 'keyboard') {
    const base = name.replace(/\.(left|right)$/, '');
    const side = name.endsWith('.left') ? ' (L)' : name.endsWith('.right') && !name.startsWith('arrow') ? ' (R)' : '';
    return (KEY_LABELS[name] || KEY_LABELS[base] || base.toUpperCase()) + side;
  }
  return name || token;
}

function keycaps(ctx, chord) {
  if (!chord.length) return h('span.muted', 'No controls');
  return h('span.keycaps', chord.map((token, i) => [i ? h('span.plus', '+') : null,
    h(`kbd${token.startsWith('keyboard:') ? '' : '.device'}`, tokenLabel(ctx, token))]));
}

function actionLabel(ctx, entry) {
  const {simple, row} = entry;
  if (simple.advanced) return row.label;
  const target = ctx.state.bindings.targets.find(t => t.id === simple.target);
  if (!target) return row.label;
  if (!simple.target.startsWith('setting:')) return target.label;
  const ops = ctx.state.bindings.settingOps[simple.target.slice(8)]?.operations || [];
  const op = ops.find(([id]) => id === simple.operation)?.[1] || simple.operation;
  return `${target.label.replace(/^Setting — /, '')}: ${op}${simple.value ? ` (${simple.value})` : ''}`;
}

function scopeLabel(ctx, row) {
  const when = row.when || {};
  const names = (when.apps || []).map(id => ctx.state.apps.find(app => app.id === id)?.name || id);
  if (when.other_apps) names.push('Other apps');
  return names.length ? names.join(', ') : 'Everywhere';
}

// --- the device diagram ------------------------------------------------------------------

function deviceDiagram(ctx) {
  const pressed = new Set(ctx.pressed);
  const bySingle = new Map();
  for (const entry of ctx.state.bindings.rows) {
    if (entry.row.enabled && entry.row.chord.length === 1 && !bySingle.has(entry.row.chord[0])) {
      bySingle.set(entry.row.chord[0], actionLabel(ctx, entry));
    }
  }
  const pad = h('div.fiveway', FIVEWAY.map(position => {
    const token = `ble.astrolabe:fiveway.${position}`;
    return h(`div.fw.fw-${position}${pressed.has(token) ? '.pressed' : ''}`, {dataset: {token}},
      h('span.fw-name', position[0].toUpperCase() + position.slice(1)),
      h('span.fw-action', bySingle.get(token) || '—'));
  }));
  return h('section.card.diagram', h('h3', 'Five-way switch'), pad,
    h('p.muted.small', 'Press a direction on the device: it lights up here. Up and Left also host the ' +
      'standalone gestures (double-tap and hold) when no daemon is connected.'));
}

// --- the editor --------------------------------------------------------------------------

function blankEntry(ctx) {
  const id = ctx.state.bindings.nextId;
  return {row: {id, label: 'New binding', enabled: true, chord: [], match: 'exact', activation: 'hold',
                priority: 0, press: [{command: 'input.mode.toggle'}], release: []},
          system: false, modified: false, simple: {target: 'input.toggle', operation: '', value: '', advanced: false},
          warning: '', isNew: true};
}

function targetSelect(ctx, draft, onchange) {
  const groups = {Modes: [], Navigation: [], Pointer: [], Settings: []};
  for (const target of ctx.state.bindings.targets) {
    const group = target.id.startsWith('setting:') ? 'Settings' : target.id.startsWith('pointer.') ? 'Pointer'
      : target.id.startsWith('navigation.') ? 'Navigation' : 'Modes';
    groups[group].push(target);
  }
  const el = h('select.select', {dataset: {key: 'binding:target'}, onchange: () => onchange(el.value)},
    Object.entries(groups).filter(([, items]) => items.length).map(([group, items]) =>
      h('optgroup', {label: group}, items.map(t => h('option', {value: t.id, selected: t.id === draft.target},
        t.label.replace(/^Setting — /, ''))))));
  return el;
}

function editor(ctx, entry, close) {
  const memory = ctx.memory;
  const draft = memory.draft?.id === entry.row.id ? memory.draft : (memory.draft = {
    id: entry.row.id, label: entry.row.label, enabled: entry.row.enabled, chord: [...entry.row.chord],
    match: entry.row.match, apps: [...(entry.row.when?.apps || [])], other: !!entry.row.when?.other_apps,
    target: entry.simple.target || 'input.toggle', operation: entry.simple.operation, value: entry.simple.value,
    advanced: entry.simple.advanced || !!entry.row.when?.executables || !!entry.row.when?.input_profiles,
  });
  const redraw = () => ctx.rerender();
  const set = (field, value) => { draft[field] = value; redraw(); };

  const settingId = draft.target.startsWith('setting:') ? draft.target.slice(8) : null;
  const ops = settingId ? ctx.state.bindings.settingOps[settingId] : null;
  if (ops && !ops.operations.some(([id]) => id === draft.operation)) draft.operation = ops.operations[0]?.[0] || '';
  const valueChoices = ops?.values || [];

  const recording = memory.recording;
  const chordBox = h(`div.chord${recording ? '.recording' : ''}`,
    keycaps(ctx, recording ? [...recording] : draft.chord),
    h('button.button.small', {onclick: () => {
      if (recording) { draft.chord = [...recording]; memory.recording = null; }
      else memory.recording = new Set();
      redraw();
    }}, icon('record', 14), recording ? 'Done' : 'Record'),
    draft.chord.length && !recording ? h('button.icon-button.small', {title: 'Clear',
      onclick: () => set('chord', [])}, icon('close', 14)) : null);

  const appChips = h('div.chips',
    h(`button.chip${!draft.apps.length && !draft.other ? '.on' : ''}`,
      {onclick: () => { draft.apps = []; draft.other = false; redraw(); }}, 'Everywhere'),
    ctx.state.apps.map(app => h(`button.chip${draft.apps.includes(app.id) ? '.on' : ''}`, {onclick: () => {
      draft.apps = draft.apps.includes(app.id) ? draft.apps.filter(a => a !== app.id) : [...draft.apps, app.id];
      redraw();
    }}, app.name)),
    h(`button.chip${draft.other ? '.on' : ''}`, {onclick: () => set('other', !draft.other)}, 'Other apps'));

  const save = async () => {
    const when = {};
    if (draft.apps.length) when.apps = draft.apps;
    if (draft.other) when.other_apps = true;
    await ctx.act('bindings.saveSimple', {id: draft.id, label: draft.label, enabled: draft.enabled,
      chord: draft.chord, match: draft.match, target: draft.target, operation: draft.operation,
      value: String(draft.value ?? ''), when: Object.keys(when).length ? when : null,
      priority: entry.row.priority || 0});
    toast('Saved', 'ok');
    memory.selected = draft.id;
    memory.draft = null;
  };

  const editJson = async () => {
    const area = h('textarea.json', {spellcheck: false, value: JSON.stringify(entry.row, null, 2)});
    const go = await dialog({title: 'Edit as JSON', wide: true, body: [
      h('p.muted', 'The complete declarative binding. Priority and context filters live here; code, ' +
                   'shell commands, and unknown targets are rejected.'), area],
      buttons: [{label: 'Cancel', value: false}, {label: 'Validate and save', value: true, primary: true}]});
    if (!go) return;
    try {
      const row = JSON.parse(area.value);
      await ctx.act('bindings.save', {row});
      toast('Saved', 'ok');
      memory.selected = row.id;
      memory.draft = null;
    } catch (error) {
      if (error instanceof SyntaxError) toast(`Not valid JSON: ${error.message}`, 'error');
    }
  };

  return h('section.editor',
    h('header.editor-head', h('h3', entry.isNew ? 'New binding' : 'Edit binding'),
      h('button.icon-button', {onclick: close, 'aria-label': 'Close'}, icon('close'))),
    draft.advanced ? h('div.hint.info', icon('info', 14),
      h('span', 'This binding uses features the form cannot show. Edit it as JSON.')) : null,
    h('div.field', h('label', 'Name'), h('input.text', {value: draft.label, dataset: {key: 'binding:label'},
      oninput: event => { draft.label = event.target.value; }})),
    h('div.field.row', h('label', 'Enabled'), toggle(draft.enabled, value => set('enabled', value))),
    h('div.field', h('label', 'Controls'), chordBox,
      recording ? h('p.muted.small', 'Press keys on the keyboard or controls on the device, then Done.') : null,
      entry.warning && !recording ? h('p.hint.info.small', entry.warning) : null),
    h('div.field', h('label', 'Extra modifier keys'),
      segmented([['exact', 'Must match exactly'], ['allow_extra_modifiers', 'May be added']], draft.match,
                value => set('match', value))),
    h('div.field', h('label', 'Applies in'), appChips),
    draft.advanced ? null : [
      h('div.field', h('label', 'Action'), targetSelect(ctx, draft, value => {
        draft.target = value;
        if (value.startsWith('pointer.')) draft.match = 'allow_extra_modifiers';
        draft.value = '';
        redraw();
      })),
      ops ? h('div.field', h('label', 'Behavior'),
        select(ops.operations, draft.operation, value => set('operation', value), {key: 'binding:op'})) : null,
      ops ? h('div.field', h('label', draft.operation === 'hold' ? 'Value while held'
                                  : draft.operation === 'cycle' ? 'Values, in order'
                                  : draft.operation === 'toggle' ? 'Two values' : 'Amount'),
        draft.operation === 'hold' && valueChoices.length
          ? select(valueChoices.map(v => [v, v]), draft.value || valueChoices[0],
                   value => set('value', value), {key: 'binding:value'})
          : h('input.text', {value: draft.value ?? '', dataset: {key: 'binding:value'},
              placeholder: draft.operation === 'cycle' ? (valueChoices.join(', ') || 'A, B, C')
                           : draft.operation === 'toggle' ? (valueChoices.length ? 'Automatic' : 'A, B') : '',
              oninput: event => { draft.value = event.target.value; }})) : null,
    ],
    h('footer.editor-foot',
      draft.advanced ? null : h('button.button.primary', {onclick: () => save().catch(() => {})}, 'Save'),
      h('button.button', {onclick: editJson}, icon('code', 15), 'JSON'),
      h('span.spacer'),
      entry.system && entry.modified ? h('button.button.ghost', {onclick: async () => {
        await ctx.act('bindings.restore', {id: entry.row.id});
        memory.draft = null;
      }}, icon('reset', 15), 'Restore') : null,
      entry.isNew ? null : h('button.button.ghost.danger', {onclick: async () => {
        const go = await dialog({title: 'Delete binding?', body: h('p', `"${entry.row.label}" will stop working.` +
          (entry.system ? ' It is a built-in binding, so it can be restored later.' : '')),
          buttons: [{label: 'Cancel', value: false}, {label: 'Delete', value: true, danger: true}]});
        if (!go) return;
        await ctx.act('bindings.delete', {id: entry.row.id});
        memory.selected = null;
        memory.draft = null;
      }}, 'Delete')));
}

// --- the page ----------------------------------------------------------------------------

export default {
  id: 'controls',
  title: 'Controls',
  icon: 'controls',

  render(ctx) {
    captureKeys(ctx);
    const b = ctx.state.bindings;
    const memory = ctx.memory;
    const entries = b.rows;
    const selected = memory.selected === '__new__' ? blankEntry(ctx)
      : entries.find(entry => entry.row.id === memory.selected);
    const close = () => { memory.selected = null; memory.draft = null; memory.recording = null; ctx.rerender(); };

    const health = h('div.capabilities', b.capabilities.map(item => h(`span.capability.${item.status.replace(/\s+/g, '-')}`,
      {title: item.detail}, h(`span.state.${item.status === 'running' ? 'healthy' : item.status === 'not present' ? 'failed' : 'waiting'}`),
      item.source === 'keyboard' ? 'Keyboard' : ctx.state.bindings.controls.find(c => c.token.startsWith(`${item.source}:`))?.device || item.source,
      h('span.muted', ` · ${item.status}`))));

    const list = h('div.binding-list', {dataset: {scroll: 'bindings'}},
      entries.map(entry => h(`button.binding${entry.row.enabled ? '' : '.disabled'}${selected?.row.id === entry.row.id ? '.active' : ''}`,
        {onclick: () => { memory.selected = entry.row.id; memory.draft = null; memory.recording = null; ctx.rerender(); }},
        h('div.binding-keys', keycaps(ctx, entry.row.chord)),
        h('div.binding-action', h('strong', actionLabel(ctx, entry)), h('span.muted.small', scopeLabel(ctx, entry.row))),
        h('div.binding-badges',
          !entry.row.enabled ? h('span.badge', 'Off') : null,
          entry.modified ? h('span.badge.accent', entry.system ? 'Changed' : 'Yours') : null))),
      h('button.binding.add', {onclick: () => { memory.selected = '__new__'; memory.draft = null; ctx.rerender(); }},
        icon('plus', 16), 'Add a binding'));

    return h('div.page-controls',
      h('header.page-head',
        h('div', h('h1', 'Controls'), h('p', 'What each button and key does while the daemon is in charge.')),
        h('div.profile-pick', h('span.muted.small', 'Profile'),
          segmented(b.profiles.map(p => [p.id, p.label]), b.profile,
                    profile => { memory.selected = null; ctx.act('bindings.profile', {profile}); }))),
      health,
      h(`div.controls-grid${selected ? '.editing' : ''}`,
        h('div.controls-main', b.profile !== 'keyboard_only' ? deviceDiagram(ctx) : null, list),
        selected ? editor(ctx, selected, close) : null));
  },

  input(ctx) {
    for (const el of document.querySelectorAll('.fw')) {
      el.classList.toggle('pressed', ctx.pressed.includes(el.dataset.token));
    }
    const recording = ctx.memory.recording;
    if (recording) {
      const selectors = new Set(ctx.state.bindings.selectors);
      let changed = false;
      for (const token of ctx.pressed) {
        if (!token.startsWith('keyboard:') && selectors.has(token) && !recording.has(token)) {
          recording.add(token);
          changed = true;
        }
      }
      if (changed) ctx.rerender();
    }
  },
};

// Keyboard capture while recording. Registered once; inert unless a recording is open.
let recordingCtx = null;
export function captureKeys(ctx) { recordingCtx = ctx; }
document.addEventListener('keydown', event => {
  const ctx = recordingCtx;
  if (!ctx?.memory.recording || ctx.route.page !== 'controls') return;
  const token = keyToken(event);
  event.preventDefault();
  if (token && ctx.state.bindings.selectors.includes(token)) {
    ctx.memory.recording.add(token);
    ctx.rerender();
  } else if (token) {
    toast(`${tokenLabel(ctx, token)} cannot be bound`, 'error');
  }
}, true);
