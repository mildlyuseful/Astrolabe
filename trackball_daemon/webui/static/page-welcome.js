// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// First-run guide. Every step reads live state and every change goes through the same actions as
// the rest of the UI; nothing here touches a host or the system until the user presses a button.

import {h, icon, mark} from './ui.js';
import {segmented, toggle} from './settings.js';
import {setUp} from './page-apps.js';

let liveRefs = {};

function step(number, title, body, done) {
  return h(`section.step${done ? '.done' : ''}`,
    h('div.step-number', done ? icon('check', 16) : String(number)),
    h('div.step-body', h('h3', title), body));
}

export default {
  id: 'welcome',
  title: 'Setup',
  icon: 'live',

  render(ctx) {
    const s = ctx.state;
    liveRefs = {connection: h('div.step-live')};
    const enabled = s.apps.filter(app => app.enabled);
    const profile = s.bindings.profile;

    return h('div.page-welcome',
      h('header.welcome-head', mark(44),
        h('div', h('h1', 'Set up Astrolabe'),
          h('p', 'Four steps. You can come back to this any time from the Device page or the tray.'))),

      step(1, 'Connect your controller', [
        h('p', 'Turn it on. Plug it in over USB, or pair it with Windows over Bluetooth — being paired ' +
               'as a mouse is fine; the daemon finds it either way.'),
        liveRefs.connection,
      ], !!ctx.live?.connection.connected),

      step(2, 'Pick how you will switch modes', [
        h('p', 'The device profile uses the five-way switch: Center toggles Pointer/3D. Keyboard only ' +
               'uses F12, with Ctrl and Shift as modifiers.'),
        segmented(s.bindings.profiles.map(p => [p.id, p.label]), profile,
                  value => ctx.act('bindings.profile', {profile: value})),
      ], true),

      step(3, 'Turn on the apps you use', [
        h('p', 'Each app needs a one-time setup. Nothing is installed until you press Set up, and every ' +
               'change is listed first.'),
        h('div.welcome-apps', s.apps.map(app => h('div.welcome-app',
          h('div', h('strong', app.name), h('span.muted.small', app.detection?.status || 'checking…')),
          toggle(app.enabled, value => ctx.act('apps.enable', {app: app.id, enabled: value}),
                 {label: `Use ${app.name}`}),
          h('button.button.small', {onclick: () => setUp(ctx, app).catch(() => {})},
            app.detection?.setupLabel || 'Set up')))),
      ], enabled.length > 0),

      step(4, 'Try it', [
        h('p', 'Focus one of your apps and roll the ball. Open the Live page to watch what the daemon ' +
               'sees; tune how each app feels under Apps.'),
        h('div.copy-row',
          h('button.button.primary', {onclick: async () => {
            await ctx.act('welcome.done');
            ctx.navigate('live');
          }}, 'Finish'),
          h('button.button', {onclick: () => ctx.navigate('apps')}, 'Go to Apps')),
      ], false));
  },

  live(ctx) {
    if (!liveRefs.connection || !ctx.live) return;
    const c = ctx.live.connection;
    liveRefs.connection.replaceChildren(
      h(`span.dot${c.connected ? '.on' : ''}`),
      h('span', c.connected ? `Connected — ${c.status}` : `Looking for it… (${c.status})`));
  },
};
