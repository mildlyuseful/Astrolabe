// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

import {h, icon, svg, dialog} from './ui.js';
import {categoryRows, categoriesFor, toggle} from './settings.js';

// Mirrors output._pointer_acceleration_multiplier, so the chart is the function the daemon runs.
function multiplier(curve, speed, onset, ramp, maxGain) {
  if (!['linear', 'smooth'].includes(curve) || speed <= onset || ramp <= 0 || maxGain <= 1) return 1;
  let progress = Math.min(1, (speed - onset) / ramp);
  if (curve === 'smooth') progress = progress * progress * (3 - 2 * progress);
  return 1 + (maxGain - 1) * progress;
}

function accelerationChart(ctx) {
  const value = id => ctx.state.global[id]?.value;
  const curve = value('pointer.acceleration.curve');
  const onset = value('pointer.acceleration.onset');
  const ramp = value('pointer.acceleration.ramp');
  const maxGain = value('pointer.acceleration.max_gain');
  const width = 320, height = 120, pad = 22;
  const top = Math.max(2, maxGain);
  const span = Math.max(1, (onset + ramp) * 1.4);
  const points = [];
  for (let i = 0; i <= 80; i++) {
    const speed = (i / 80) * span;
    const m = multiplier(curve, speed, onset, ramp, maxGain);
    points.push(`${pad + (speed / span) * (width - pad * 2)},${height - pad - ((m - 1) / (top - 1)) * (height - pad * 2)}`);
  }
  return h('figure.curve',
    svg('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Acceleration curve'},
      svg('line', {x1: pad, y1: height - pad, x2: width - pad, y2: height - pad, class: 'axis'}),
      svg('line', {x1: pad, y1: pad, x2: pad, y2: height - pad, class: 'axis'}),
      svg('polyline', {points: points.join(' '), class: curve === 'off' ? 'flat' : 'line'}),
      svg('text', {x: width - pad, y: height - 6, 'text-anchor': 'end'}, 'ball speed (rad/s)'),
      svg('text', {x: pad + 4, y: pad - 6}, `×${(+top).toFixed(1)}`)),
    h('figcaption.muted.small', curve === 'off'
      ? 'Off: the cursor moves the same distance for every turn of the ball.'
      : `Cursor speed rises from ×1 above ${onset} rad/s to ×${maxGain} at ${+(onset + ramp).toFixed(2)} rad/s.`));
}

function section(ctx, category) {
  return h('section.card.settings-card', {id: `section-${category.id}`},
    h('header', h('h3', category.title), category.summary ? h('p.muted', category.summary) : null),
    category.id === 'transform' ? h('p.muted.small', 'Roll the ball away from you and watch the Live ' +
      'page: it should turn the same way. If an axis is wrong, change its source here; swapping one ' +
      'swaps the other so nothing is lost.') : null,
    categoryRows(ctx, category.id, null),
    category.id === 'pointer' ? accelerationChart(ctx) : null);
}

export default {
  id: 'device',
  title: 'Device',
  icon: 'device',

  render(ctx) {
    const categories = categoriesFor(ctx, 'device', null);
    const startup = ctx.state.startup;
    return h('div.page-device',
      h('header.page-head',
        h('div', h('h1', 'Device'), h('p', 'Your controller, how it sits on the desk, and how the cursor feels.')),
        h('nav.jump', categories.map(c => h('a', {href: `#/device`, onclick: event => {
          event.preventDefault();
          document.getElementById(`section-${c.id}`)?.scrollIntoView({behavior: 'smooth', block: 'start'});
        }}, c.title)))),
      h('div.device-grid',
        categories.map(category => section(ctx, category)),
        h('section.card.settings-card',
          h('header', h('h3', 'System')),
          startup === null ? null : h('div.setting',
            h('div.setting-text', h('label', 'Start at login'),
              h('p', 'Run the daemon when you sign in to Windows.')),
            h('div.setting-control', toggle(startup, enabled => ctx.act('startup.set', {enabled}),
                                            {label: 'Start at login'})),
            h('div.source')),
          h('div.setting',
            h('div.setting-text', h('label', 'Setup guide'), h('p', 'Walk through connecting and enabling apps again.')),
            h('div.setting-control', h('button.button', {onclick: () => ctx.navigate('welcome')}, 'Open')),
            h('div.source')),
          h('div.setting',
            h('div.setting-text', h('label', 'Factory defaults'),
              h('p', 'Reset every default on this page and on All apps. Per-app settings and bindings stay.')),
            h('div.setting-control', h('button.button.ghost.danger', {onclick: async () => {
              const go = await dialog({title: 'Reset defaults?', body: h('p', 'Orientation, pointer, control ' +
                'panel and All apps settings go back to factory values. The device name and address stay.'),
                buttons: [{label: 'Cancel', value: false}, {label: 'Reset', value: true, danger: true}]});
              if (go) ctx.act('settings.resetGlobals');
            }}, icon('reset', 15), 'Reset')),
            h('div.source')))));
  },
};
