// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

import {h, icon} from './ui.js';
import {Ball} from './ball.js';
import {segmented} from './settings.js';

const ball = new Ball({size: 320});
let refs = {};

const HEALTH_LABELS = {healthy: 'Healthy', waiting: 'Waiting', degraded: 'Degraded',
                       failed: 'Failed', disabled: 'Off'};

function meter(axis) {
  const bar = h('div.meter-fill');
  const value = h('span.meter-value', '0.00');
  return {el: h(`div.meter.axis-${axis.toLowerCase()}`, h('span.meter-axis', axis),
                h('div.meter-track', bar), value), bar, value};
}

export default {
  id: 'live',
  title: 'Live',
  icon: 'live',

  render(ctx) {
    const meters = ['X', 'Y', 'Z'].map(meter);
    ball.onspeed = speed => meters.forEach((m, i) => {
      const v = speed[i];
      m.bar.style.width = `${Math.min(50, Math.abs(v) * 8)}%`;
      m.bar.style.marginLeft = v < 0 ? `${50 - Math.min(50, Math.abs(v) * 8)}%` : '50%';
      m.value.textContent = v.toFixed(2);
    });

    const stage = h('div.ball-stage');
    ball.mount(stage);
    refs = {
      mode: h('div.mode-switch'),
      navModes: h('div.nav-modes'),
      app: h('div.now-app'),
      context: h('p.now-context'),
      help: h('p.now-help'),
      binding: h('p.now-binding'),
      device: h('div.kv'),
      services: h('div.services'),
    };

    const welcome = ctx.state.firstRun ? h('div.banner',
      h('div', h('strong', 'Welcome to Astrolabe'),
        h('p', 'Connect your controller, turn on the apps you use, and you are ready to go.')),
      h('button.button.primary', {onclick: () => ctx.navigate('welcome')}, 'Start setup')) : null;

    return h('div.page-live',
      h('header.page-head',
        h('div', h('h1', 'Live'), h('p', 'What your controller and the daemon are doing right now.'))),
      welcome,
      h('div.live-grid',
        h('section.card.hero',
          stage,
          h('div.meters', meters.map(m => m.el)),
          h('div.hero-controls', refs.mode, refs.navModes),
          h('div.hero-foot',
            h('button.button.ghost', {onclick: () => ball.reset(), title: 'Square up the drawing'},
              icon('target', 16), 'Square up'),
            h('button.button.ghost', {onclick: () => ctx.act('device.recenter'),
                                      title: 'Reset the 3D view in the focused app'},
              icon('refresh', 16), 'Recenter view'))),
        h('div.live-side',
          h('section.card.now', h('h3', 'Now'), refs.app, refs.context, refs.help, refs.binding),
          h('section.card', h('h3', 'Controller'), refs.device),
          h('section.card', h('h3', 'Services'), refs.services))));
  },

  live(ctx) {
    if (!ctx.live || !refs.mode) return;
    const {runtime, connection, connectedApps, health} = ctx.live;
    const focused = ctx.state.apps.find(app => app.id === runtime.focusedApp);

    refs.mode.replaceChildren(segmented(
      [['pointer', 'Pointer'], ['3d', '3D']], runtime.inputMode,
      mode => ctx.act('runtime.inputMode', {mode}), {className: '.large'}));

    const modes = focused?.modes || ['orbit'];
    refs.navModes.replaceChildren(runtime.inputMode === '3d' && modes.length > 1 ? segmented(
      modes.map(mode => [mode, ctx.state.schema.choiceLabels[mode] || mode]), runtime.navigationMode,
      mode => ctx.act('runtime.navigationMode', {mode})) : h('span'));

    refs.app.replaceChildren(
      h('span.app-badge', (focused?.name || runtime.executable || 'Desktop').slice(0, 1)),
      h('div', h('strong', focused?.name || 'No 3D app in focus'),
        h('span.muted', focused ? (focused.enabled ? 'Integration on' : 'Integration off — turn it on in Apps')
                                : runtime.executable || 'Focus a supported app to navigate in 3D')));
    refs.context.textContent = runtime.context;
    refs.help.textContent = runtime.help;
    refs.binding.textContent = runtime.binding;

    refs.device.replaceChildren(
      h('div', h('span', 'Status'), h('strong', connection.connected ? 'Connected' : 'Searching')),
      h('div', h('span', 'Link'), h('span.small', connection.status)),
      h('div', h('span', 'Power'), h('span', connection.power)));

    const serviceName = id => id === 'navigation-broker' ? 'Add-on broker'
      : ctx.state.apps.find(app => app.id === id)?.name || id;
    const rows = Object.entries(health).map(([id, item]) => h('div.service',
      h(`span.state.${item.state}`), h('div', h('strong', serviceName(id)),
        h('span.muted.small', item.detail)), h('span.small', HEALTH_LABELS[item.state] || item.state)));
    const addins = Object.entries(connectedApps).map(([id, version]) => h('div.service',
      h('span.state.healthy'), h('div', h('strong', ctx.state.apps.find(a => a.id === id)?.name || id),
        h('span.muted.small', `add-on ${version}`)), h('span.small', 'Connected')));
    refs.services.replaceChildren(...(rows.length || addins.length ? [...addins, ...rows]
      : [h('p.muted', 'Nothing running yet. Turn on an app in Apps.')]));
  },

  motion(_ctx, delta) {
    ball.add(delta);
  },
};
