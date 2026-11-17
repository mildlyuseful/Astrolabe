// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

import {h, icon, dialog, toast, copyText} from './ui.js';
import {toggle, categoryRows, categoriesFor} from './settings.js';

const SETUP_TAB = {id: 'setup', title: 'Setup'};

function appStatus(ctx, app) {
  const version = ctx.live?.connectedApps?.[app.id];
  if (version) return {tone: 'connected', text: `Connected · ${version}`};
  if (app.detection?.status === 'not detected' && !app.installed) return {tone: 'missing', text: 'Not found'};
  if (app.enabled) return {tone: 'on', text: app.detection?.updateAvailable ? 'Update ready' : 'On'};
  return {tone: 'off', text: app.installed ? 'Off' : 'Not set up'};
}

function listItem(ctx, id, name, status, active) {
  return h(`a.app-item${active ? '.active' : ''}`, {href: `#/apps/${id}/${ctx.memory.appTab || ''}`},
    h(`span.dot.${status.tone}`), h('span.app-name', name), h('span.app-state', status.text));
}

function appList(ctx, selected) {
  const groups = [['supported', 'Supported'], ['experimental', 'Experimental']];
  return h('aside.app-list', {dataset: {scroll: 'app-list'}},
    h(`a.app-item.all${selected === 'all' ? '.active' : ''}`, {href: '#/apps/all'},
      h('span.all-icon', icon('apps', 16)), h('div.app-name', h('strong', 'All apps'),
        h('span.muted.small', 'Defaults every app follows'))),
    groups.map(([tier, title]) => [
      h('h5', title),
      ctx.state.apps.filter(app => app.tier === tier).map(app =>
        listItem(ctx, app.id, app.name, appStatus(ctx, app), app.id === selected)),
    ]));
}

export async function setUp(ctx, app) {
  const preflight = await ctx.act('apps.preflight', {app: app.id});
  let allowUnsupported = false;
  if (preflight.unsupported) {
    allowUnsupported = await dialog({title: `${app.name}: unsupported version`,
      body: h('p.prewrap', preflight.unsupported),
      buttons: [{label: 'Cancel', value: false}, {label: 'Set up anyway', value: true, danger: true}]});
    if (!allowUnsupported) return;
  }
  if (preflight.confirmation) {
    const go = await dialog({title: `Before setting up ${app.name}`, body: h('p.prewrap', preflight.confirmation),
      buttons: [{label: 'Cancel', value: false}, {label: 'Continue', value: true, primary: true}]});
    if (!go) return;
  }
  let installStartup;
  if (preflight.asksStartup) {
    installStartup = await dialog({title: 'Start with Blender?',
      body: [h('p', 'The add-on is copied into every Blender version found.'),
             h('p', 'Blender can also turn it on at every launch, using a small startup script. Without it, ' +
                    'enable "Trackball Nav" once in Preferences → Add-ons.')],
      buttons: [{label: 'Cancel', value: null}, {label: 'Install only', value: false},
                {label: 'Install and auto-enable', value: true, primary: true}]});
    if (installStartup === null) return;
  }
  toast(`Setting up ${app.name}…`);
  const result = await ctx.act('apps.setup', {app: app.id, allowUnsupported, installStartup});
  const copies = [...result.copyables];
  if (app.id === 'onshape' && result.ok) {
    const script = await ctx.act('onshape.userscript');
    copies.push({label: 'Copy userscript', text: script.script});
  }
  await dialog({title: result.ok ? `${app.name} is set up` : `${app.name} setup did not finish`, wide: true,
    body: [h(`div.result.${result.ok ? 'ok' : 'error'}`, icon(result.ok ? 'check' : 'warn', 18),
             h('p.prewrap', result.message)),
           copies.length ? h('div.copy-row', copies.map(item =>
             h('button.button', {onclick: () => copyText(item.text, `${item.label}: copied`)},
               icon('copy', 15), item.label))) : null]});
}

async function showUserscript(ctx) {
  const script = await ctx.act('onshape.userscript');
  await dialog({title: 'Onshape userscript', wide: true, body: [
    h('p', 'Under-cursor orbit in Onshape needs this small script in your browser\'s userscript manager ' +
           '(Violentmonkey or Tampermonkey). It only reports where the pointer is over the Onshape view.'),
    h('div.copy-row',
      h('button.button.primary', {onclick: () => copyText(script.script, 'Userscript copied')},
        icon('copy', 15), 'Copy userscript'),
      h('a.button', {href: script.bridgeUrl, target: '_blank', rel: 'noopener'}, 'Open bridge page')),
    h('p.prewrap.muted', script.instructions)]});
}

function setupTab(ctx, app) {
  const d = app.detection;
  const health = ctx.live?.health?.[app.id];
  const compatTone = {unsupported: 'error', unverified: 'warning'}[d?.compatibility] || '';
  return h('div.setup',
    h('div.facts',
      h('div', h('span', 'On this PC'), h('strong', d ? d.status : 'Checking…')),
      d?.compatibilityMessage ? h(`div.${compatTone}`, h('span', 'Host version'), h('span', d.compatibilityMessage)) : null,
      h('div', h('span', 'Verified with'), h('span', app.verified || '—')),
      d?.installedVersion ? h('div', h('span', 'Add-on'), h('span',
        `v${d.installedVersion}${d.updateAvailable ? ' — update available' : ''}`)) : null,
      health ? h('div', h('span', 'Runtime'), h('span', h(`span.state.${health.state}`), ` ${health.state} — ${health.detail}`)) : null,
      h('div', h('span', 'How it installs'), h('span', app.installModel)),
      h('div', h('span', 'Security'), h('span', app.security)),
      h('div', h('span', 'Release tier'), h('span', app.tierSummary))),
    h('details.instructions',
      h('summary', 'Step-by-step instructions'),
      h('pre', app.instructions),
      h('div.copy-row',
        h('button.button', {onclick: () => copyText(app.instructions, 'Instructions copied')},
          icon('copy', 15), 'Copy instructions'),
        app.id === 'onshape' ? h('button.button', {onclick: () => showUserscript(ctx)},
          icon('code', 15), 'Onshape userscript') : null)));
}

function detail(ctx, app, tab) {
  const tabs = [...(app ? [SETUP_TAB] : []), ...categoriesFor(ctx, 'apps', app?.id)];
  const active = tabs.find(t => t.id === tab) || tabs[0];
  const base = `#/apps/${app ? app.id : 'all'}/`;

  const head = app ? h('header.detail-head',
    h('div.detail-title',
      h('h2', app.name),
      h(`span.pill.${app.tier}`, app.tier === 'supported' ? 'Supported' : 'Experimental'),
      h('p.muted', app.detection?.status || 'Checking what is installed…')),
    h('div.detail-actions',
      h('label.enable', h('span', app.enabled ? 'On' : 'Off'),
        toggle(app.enabled, enabled => ctx.act('apps.enable', {app: app.id, enabled}), {label: `Use ${app.name}`})),
      h('button.button.primary', {onclick: () => setUp(ctx, app).catch(() => {})},
        app.detection?.setupLabel || 'Set up')))
    : h('header.detail-head',
        h('div.detail-title', h('h2', 'All apps'),
          h('p.muted', 'Every app follows these unless you set something for it specifically.')),
        h('div.detail-actions', h('button.button.ghost', {onclick: async () => {
          const go = await dialog({title: 'Reset all apps?', body: h('p', 'Every default goes back to the ' +
            'factory value. Settings you made for individual apps stay as they are.'),
            buttons: [{label: 'Cancel', value: false}, {label: 'Reset', value: true, danger: true}]});
          if (go) ctx.act('settings.resetGlobals');
        }}, icon('reset', 15), 'Reset defaults')));

  const category = ctx.state.schema.categories.find(c => c.id === active.id);
  const body = active.id === 'setup' ? setupTab(ctx, app) : h('div.settings',
    category?.summary ? h('p.section-summary', category.summary) : null,
    categoryRows(ctx, active.id, app?.id, app?.hints || []),
    app ? h('div.tab-foot',
      h('button.link-button', {onclick: () => ctx.act('settings.linkAll', {app: app.id})},
        app.allLinked ? 'Customize every setting for this app' : 'Make everything follow All apps'),
      h('button.link-button.danger', {onclick: async () => {
        const go = await dialog({title: `Reset ${app.name}?`, body: h('p',
          `Every ${app.name} setting goes back to its factory value. Whether it is on, and its installed add-on, stay as they are.`),
          buttons: [{label: 'Cancel', value: false}, {label: 'Reset', value: true, danger: true}]});
        if (go) ctx.act('settings.resetApp', {app: app.id});
      }}, 'Reset this app')) : null);

  return h('section.app-detail', {dataset: {scroll: 'app-detail'}},
    head,
    h('nav.tabs', tabs.map(t => h(`a.tab${t.id === active.id ? '.active' : ''}`,
      {href: base + t.id, onclick: () => { ctx.memory.appTab = t.id; }}, t.title))),
    body);
}

export default {
  id: 'apps',
  title: 'Apps',
  icon: 'apps',

  render(ctx) {
    const [selected = ctx.memory.app || 'all', tab = ctx.memory.appTab] = ctx.route.parts;
    ctx.memory.app = selected;
    if (tab) ctx.memory.appTab = tab;
    const app = ctx.state.apps.find(item => item.id === selected) || null;
    ctx.runHint = action => action === 'onshape.userscript' && showUserscript(ctx);
    return h('div.page-apps',
      h('header.page-head',
        h('div', h('h1', 'Apps'), h('p', 'Turn on the apps you use, then tune how each one feels.')),
        h('button.button.ghost', {onclick: () => { ctx.act('apps.detect'); toast('Checking installed apps…'); }},
          icon('refresh', 15), 'Check again')),
      h('div.split', appList(ctx, app ? app.id : 'all'), detail(ctx, app, tab)));
  },

  live(ctx) {
    // Connection badges change with the daemon, not with settings: refresh only the list.
    const list = document.querySelector('.app-list');
    if (!list || !ctx.state) return;
    for (const app of ctx.state.apps) {
      const item = list.querySelector(`a[href^="#/apps/${app.id}/"]`);
      if (!item) continue;
      const status = appStatus(ctx, app);
      item.querySelector('.dot').className = `dot ${status.tone}`;
      item.querySelector('.app-state').textContent = status.text;
    }
  },
};
