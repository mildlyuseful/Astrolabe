// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// Settings rows generated from the daemon's schema.
//
// Every SettingSpec renders here: the control comes from its kind and choices, the row's link and
// reset buttons from where its value comes from. A new setting in settings_schema.py appears with
// no change to this file. Paired axis settings (".source" + ".invert") share one row.

import {h, icon, debounce, formatNumber, titleCase} from './ui.js';

const AXES = ['X', 'Y', 'Z'];

export function specsIn(state, category, appId) {
  return state.schema.settings.filter(spec => spec.category === category &&
    (appId ? spec.apps.includes(appId) : spec.scope !== 'global_and_app' || true));
}

export function choiceLabel(state, spec, value) {
  if (value === null || value === undefined) return 'Inherit';
  if (spec.id.includes('pivot') && state.schema.pivotLabels[value]) return state.schema.pivotLabels[value];
  if (spec.id.endsWith('.source') && [0, 1, 2].includes(value)) return AXES[value];
  return state.schema.choiceLabels[value] ?? titleCase(value);
}

function viewOf(ctx, spec, appId) {
  if (!appId) return ctx.state.global[spec.id];
  return ctx.state.apps.find(app => app.id === appId).settings[spec.id];
}

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// --- controls ----------------------------------------------------------------------------

export function toggle(checked, onchange, {key, label} = {}) {
  return h('button.switch', {role: 'switch', 'aria-checked': String(!!checked), 'aria-label': label,
                             dataset: key ? {key} : undefined,
                             onclick: () => onchange(!checked)}, h('span'));
}

export function segmented(options, value, onchange, {key, className = ''} = {}) {
  return h(`div.segmented${className}`, {role: 'radiogroup', dataset: key ? {key} : undefined},
    options.map(([optionValue, label, extra]) => h(`button${same(optionValue, value) ? '.on' : ''}`, {
      role: 'radio', 'aria-checked': String(same(optionValue, value)), class: extra,
      onclick: () => { if (!same(optionValue, value)) onchange(optionValue); },
    }, label)));
}

export function select(options, value, onchange, {key} = {}) {
  const el = h('select.select', {dataset: key ? {key} : undefined,
                                 onchange: () => onchange(options[el.selectedIndex][0])},
    options.map(([optionValue, label]) => h('option', {selected: same(optionValue, value)}, label)));
  return el;
}

function numberField(spec, value, commit, key) {
  const integer = spec.kind === 'integer';
  const parse = text => {
    const n = integer ? parseInt(text, 10) : parseFloat(text);
    if (!isFinite(n)) return null;
    if (spec.min != null && n < spec.min) return null;
    if (spec.max != null && n > spec.max) return null;
    return n;
  };
  const step = integer ? 1 : Math.abs(value) >= 20 ? 1 : Math.abs(value) >= 2 ? 0.1 : 0.05;
  const live = debounce(() => {
    const n = parse(input.value);
    if (n !== null && n !== value) commit(n);
  }, 450);
  const input = h('input.number', {
    type: 'number', step, value: formatNumber(value), min: spec.min ?? undefined,
    max: spec.max ?? undefined, dataset: {key}, inputMode: 'decimal',
    oninput: live,
    onchange: () => {
      const n = parse(input.value);
      if (n === null) input.value = formatNumber(value);
      else live.flush();
    },
    onkeydown: event => { if (event.key === 'Enter') input.blur(); },
  });
  if (spec.min == null || spec.max == null) return input;
  const slider = h('input.slider', {
    type: 'range', min: spec.min, max: spec.max, step: integer ? 1 : (spec.max - spec.min) / 200,
    value, dataset: {key: `${key}~slider`},
    oninput: () => { input.value = formatNumber(+slider.value); live(); },
  });
  return h('div.number-slider', slider, input);
}

function textField(value, commit, key) {
  const input = h('input.text', {
    value: value ?? '', dataset: {key}, spellcheck: false,
    onchange: () => { if (input.value !== value) commit(input.value.trim()); },
    onkeydown: event => { if (event.key === 'Enter') input.blur(); },
  });
  return input;
}

function orderedList(ctx, spec, value, commit, key) {
  const list = [...value];
  const label = item => choiceLabel(ctx.state, spec, item);
  const move = (index, delta) => {
    const next = [...list];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    commit(next);
  };
  const remaining = spec.choices.filter(choice => !list.includes(choice));
  return h('div.ordered', {dataset: {key}},
    h('ol', list.map((item, index) => h('li',
      h('span.ordinal', String(index + 1)), h('span.ordered-label', label(item)),
      h('span.ordered-actions',
        h('button.icon-button.small', {disabled: index === 0, title: 'Try earlier',
                                       onclick: () => move(index, -1)}, icon('up', 14)),
        h('button.icon-button.small', {disabled: index === list.length - 1, title: 'Try later',
                                       onclick: () => move(index, 1)}, icon('down', 14)),
        h('button.icon-button.small', {title: 'Remove',
                                       onclick: () => commit(list.filter((_, i) => i !== index))},
          icon('close', 14)))))),
    list.length === 0 ? h('p.muted.small', 'Empty: nothing is tried after the primary pivot.') : null,
    remaining.length ? h('div.ordered-add',
      select([['', 'Add a fallback…'], ...remaining.map(item => [item, label(item)])], '',
             item => item && commit([...list, item]))) : null);
}

export function control(ctx, spec, view, commit, key) {
  const value = view.value;
  const choices = (view.choices && view.choices.length ? view.choices : spec.choices)
    .filter(choice => choice !== 'default' && !(spec.id === 'input.mode.default' &&
                                               ['cube', 'cursor'].includes(choice)));
  if (spec.control === 'ordered_list') return orderedList(ctx, spec, value || [], commit, key);
  if (spec.kind === 'boolean') return toggle(!!value, commit, {key, label: spec.label});
  if (choices.length) {
    const options = choices.map(choice => [choice, choiceLabel(ctx.state, spec, choice),
      spec.id.endsWith('.source') ? `axis-${AXES[choice]?.toLowerCase()}` : undefined]);
    const short = options.length <= 4 && options.every(([, label]) => label.length <= 12);
    return short ? segmented(options, value, commit, {key}) : select(options, value, commit, {key});
  }
  if (spec.kind === 'integer' || spec.kind === 'number') return numberField(spec, value, commit, key);
  return textField(value, commit, key);
}

// --- rows --------------------------------------------------------------------------------

function sourceButtons(ctx, specs, appId) {
  const views = specs.map(spec => viewOf(ctx, spec, appId));
  const differs = specs.some((spec, i) => !same(views[i].value, views[i].system));
  const reset = () => Promise.all(specs.map(spec =>
    ctx.act('settings.reset', {app: appId ?? null, id: spec.id})));
  const resetButton = differs ? h('button.icon-button.small.reset', {
    title: `Reset to the factory default (${specs.map(s => choiceLabel(ctx.state, s, viewOf(ctx, s, appId).system)).join(', ')})`,
    onclick: reset}, icon('reset', 15)) : h('span.reset-space');
  if (!appId) {
    return h('div.source', resetButton);
  }
  const linked = views.every(view => view.linked);
  const unsupported = linked && views.some(view => !view.globalCompatible);
  return h('div.source', resetButton,
    h(`button.chip.link${linked ? '' : '.custom'}`, {
      title: linked ? 'Follows All apps. Click to set a value for this app only.'
                    : 'Set for this app. Click to follow All apps again.',
      onclick: () => Promise.all(specs.map(spec => ctx.act('settings.link', {app: appId, id: spec.id}))),
    }, icon(linked ? 'link' : 'unlink', 14), linked ? (unsupported ? 'Own default' : 'All apps') : 'This app'));
}

export function settingRow(ctx, spec, appId, {hint} = {}) {
  const view = viewOf(ctx, spec, appId);
  const key = `${appId || 'global'}:${spec.id}`;
  const commit = value => ctx.act('settings.set', {app: appId ?? null, id: spec.id, value});
  const linked = appId && view.linked;
  return h(`div.setting${linked ? '.linked' : ''}`,
    h('div.setting-text',
      h('label', spec.label),
      spec.help && spec.help !== spec.label ? h('p', spec.help) : null,
      hint ? hintNote(ctx, hint) : null),
    h('div.setting-control', control(ctx, spec, view, commit, key)),
    sourceButtons(ctx, [spec], appId));
}

function hintNote(ctx, hint) {
  return h(`div.hint.${hint.level}`, icon(hint.level === 'warning' ? 'warn' : 'info', 14),
    h('span', hint.text),
    hint.action ? h('button.link-button', {onclick: () => ctx.runHint?.(hint.action)}, 'Show me') : null);
}

// Paired source + invert rows: one row per action, one decision per row.
function axisRow(ctx, label, sourceSpec, invertSpec, appId) {
  const source = viewOf(ctx, sourceSpec, appId);
  const invert = invertSpec ? viewOf(ctx, invertSpec, appId) : null;
  const key = `${appId || 'global'}:${sourceSpec.id}`;
  const linked = appId && source.linked && (!invert || invert.linked);
  return h(`div.setting.axis-row${linked ? '.linked' : ''}`,
    h('div.setting-text', h('label', label)),
    h('div.setting-control.axis-control',
      segmented([0, 1, 2].map(i => [i, AXES[i], `axis-${AXES[i].toLowerCase()}`]), source.value,
        value => ctx.act('settings.set', {app: appId ?? null, id: sourceSpec.id, value}),
        {key, className: '.axes'}),
      invert ? h('button.invert' + (invert.value ? '.on' : ''), {
        title: 'Reverse the direction', 'aria-pressed': String(!!invert.value), dataset: {key: `${key}:inv`},
        onclick: () => ctx.act('settings.set', {app: appId ?? null, id: invertSpec.id, value: !invert.value}),
      }, invert.value ? '−' : '+') : null),
    sourceButtons(ctx, invertSpec ? [sourceSpec, invertSpec] : [sourceSpec], appId));
}

const ACTION_NAMES = {
  pan_x: 'Pan X', pan_y: 'Pan Y', translate_x: 'Move X', translate_y: 'Move Y', translate_z: 'Move Z',
};
const GROUP_NAMES = {orbit: 'Orbit', pan: 'Pan', zoom: 'Zoom', camera: 'Camera turn', fly: 'Fly',
                     walk: 'Walk', object: 'Object'};

// Group axis settings by their mode, pairing each ".source" with its ".invert".
function axisGroups(ctx, specs, appId) {
  const groups = new Map();
  for (const spec of specs.filter(spec => spec.id.endsWith('.source'))) {
    const stem = spec.id.slice(0, -'.source'.length);
    const parts = stem.split('.');
    const group = parts[spec.id.startsWith('navigation.routing.') ? 2 : 0];
    const action = spec.id.startsWith('navigation.routing.') ? parts.slice(3).join('.') : parts.at(-1);
    const invert = specs.find(other => other.id === `${stem}.invert`);
    const label = ACTION_NAMES[action] || (action ? titleCase(action) : GROUP_NAMES[group] || titleCase(group));
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(axisRow(ctx, label, spec, invert, appId));
  }
  return [...groups].map(([group, rows]) =>
    h('div.axis-group', h('h4', GROUP_NAMES[group] || titleCase(group)), rows));
}

// One category's rows, in schema order. Hints from the daemon attach to the row they name.
export function categoryRows(ctx, category, appId, hints = []) {
  const specs = ctx.state.schema.settings.filter(spec => spec.category === category &&
    (appId ? spec.apps.includes(appId) : true));
  const paired = specs.filter(spec => /\.(source|invert)$/.test(spec.id) &&
    specs.some(other => other.id === spec.id.replace(/\.(source|invert)$/, '.source')));
  if (paired.length && paired.length === specs.length) {
    if (category === 'transform') {
      return specs.filter(spec => spec.id.endsWith('.source')).map(spec => {
        const axis = spec.id.split('.').at(-2).toUpperCase();
        return axisRow(ctx, `Logical ${axis}`, spec,
          specs.find(other => other.id === spec.id.replace('.source', '.invert')), appId);
      });
    }
    return axisGroups(ctx, specs, appId);
  }
  return specs.filter(spec => !paired.includes(spec)).map(spec =>
    settingRow(ctx, spec, appId, {hint: hints.find(hint => hint.setting === spec.id)}));
}

export function categoriesFor(ctx, page, appId) {
  const known = ctx.state.schema.categories.filter(category => category.page === page);
  const present = new Set(ctx.state.schema.settings
    .filter(spec => appId ? spec.apps.includes(appId)
                          : page === 'apps' ? spec.scope === 'global_and_app'
                                            : spec.scope !== 'global_and_app')
    .map(spec => spec.category));
  const listed = known.filter(category => present.has(category.id));
  // A category the daemon has but CATEGORIES does not place still shows, after the known ones.
  const placed = new Set(ctx.state.schema.categories.map(category => category.id));
  const extra = [...present].filter(id => !placed.has(id))
    .map(id => ({id, title: titleCase(id), page, summary: ''}));
  return [...listed, ...(page === 'apps' ? extra : [])];
}
