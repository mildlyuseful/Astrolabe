// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// A ball that turns exactly as the device reports. Rotation deltas arrive in radians about the
// housing axes (+X right, +Y away from you, +Z up) and are integrated into a quaternion, so the
// drawing is a direct check of the firmware's fusion: roll the ball, and this one should follow.

const TAU = Math.PI * 2;
const ELEVATION = 0.55; // the camera looks down at the desk at about 31 degrees

function multiply(a, b) {
  return [
    a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
    a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
    a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
    a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
  ];
}

function rotate(q, v) {
  const [w, x, y, z] = q;
  const ix = w * v[0] + y * v[2] - z * v[1];
  const iy = w * v[1] + z * v[0] - x * v[2];
  const iz = w * v[2] + x * v[1] - y * v[0];
  const iw = -x * v[0] - y * v[1] - z * v[2];
  return [ix * w + iw * -x + iy * -z - iz * -y,
          iy * w + iw * -y + iz * -x - ix * -z,
          iz * w + iw * -z + ix * -y - iy * -x];
}

function fromRotation(rx, ry, rz) {
  const angle = Math.hypot(rx, ry, rz);
  if (angle < 1e-9) return [1, 0, 0, 0];
  const s = Math.sin(angle / 2) / angle;
  return [Math.cos(angle / 2), rx * s, ry * s, rz * s];
}

// World (X right, Y away, Z up) to screen (x right, y up) and depth (negative faces the camera).
const ce = Math.cos(ELEVATION), se = Math.sin(ELEVATION);
function project(p) {
  return [p[0], p[1] * se + p[2] * ce, p[1] * ce - p[2] * se];
}

function circle(points, map) {
  const out = [];
  for (let i = 0; i <= points; i++) out.push(map((i / points) * TAU));
  return out;
}

// Graticule in the ball's own frame: six meridians and five parallels.
const LINES = [];
for (let m = 0; m < 6; m++) {
  const a = (m / 6) * Math.PI;
  LINES.push({kind: 'meridian', points: circle(72, t => [Math.cos(a) * Math.sin(t), Math.sin(a) * Math.sin(t), Math.cos(t)])});
}
for (const lat of [-60, -30, 0, 30, 60]) {
  const r = Math.cos((lat * Math.PI) / 180), z = Math.sin((lat * Math.PI) / 180);
  LINES.push({kind: lat === 0 ? 'equator' : 'parallel', points: circle(72, t => [r * Math.cos(t), r * Math.sin(t), z])});
}

export class Ball {
  constructor({size = 300} = {}) {
    this.q = [1, 0, 0, 0];
    this.speed = [0, 0, 0];
    this.size = size;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'ball-canvas';
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', 'Live view of the ball rotation');
    this.frame = null;
    this.lastMotion = 0;
    this.resize();
  }

  resize() {
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = this.size * ratio;
    this.canvas.height = this.size * ratio;
    this.canvas.style.width = `${this.size}px`;
    this.canvas.style.height = `${this.size}px`;
    this.ratio = ratio;
  }

  // Reattach after a page re-render without losing the orientation. Drawing waits a frame, until
  // the canvas is in the document and its theme colours resolve.
  mount(parent) {
    parent.append(this.canvas);
    this.schedule();
  }

  add([rx, ry, rz], dt = 1 / 30) {
    this.q = multiply(fromRotation(rx, ry, rz), this.q);
    const n = Math.hypot(...this.q);
    this.q = this.q.map(c => c / n);
    for (let i = 0; i < 3; i++) this.speed[i] = this.speed[i] * 0.4 + ([rx, ry, rz][i] / dt) * 0.6;
    this.lastMotion = performance.now();
    this.schedule();
  }

  reset() {
    this.q = [1, 0, 0, 0];
    this.speed = [0, 0, 0];
    this.schedule();
  }

  schedule() {
    if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = null; this.tick(); });
  }

  tick() {
    const idle = performance.now() - this.lastMotion;
    if (idle > 120) this.speed = this.speed.map(s => s * 0.82);
    this.draw();
    if (!this.canvas.isConnected) return;
    this.onspeed?.(this.speed);
    if (this.speed.some(s => Math.abs(s) > 0.01)) this.schedule();
  }

  draw() {
    // Off the page (another tab is showing) there is nothing to draw; the orientation keeps
    // integrating and is drawn when the canvas comes back.
    if (!this.canvas.isConnected) return;
    const ctx = this.canvas.getContext('2d');
    const style = getComputedStyle(this.canvas);
    const color = name => style.getPropertyValue(name).trim() || '#888';
    const s = this.size * this.ratio, c = s / 2, r = s * 0.4;
    ctx.clearRect(0, 0, s, s);

    // Shadow on the desk, then the ball body.
    ctx.save();
    ctx.fillStyle = color('--ball-shadow');
    ctx.beginPath();
    ctx.ellipse(c, c + r * 1.02, r * 0.85, r * 0.16, 0, 0, TAU);
    ctx.fill();
    ctx.restore();

    const body = ctx.createRadialGradient(c - r * 0.35, c - r * 0.45, r * 0.05, c, c, r);
    body.addColorStop(0, color('--ball-light'));
    body.addColorStop(1, color('--ball-dark'));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(c, c, r, 0, TAU);
    ctx.fill();

    const speed = Math.min(1, Math.hypot(...this.speed) / 6);
    const line = color('--ball-line'), accent = color('--accent');
    for (const pass of ['back', 'front']) {
      for (const item of LINES) {
        ctx.beginPath();
        let pen = false;
        for (const p of item.points) {
          const [x, y, depth] = project(rotate(this.q, p));
          const front = depth < 0;
          if (front === (pass === 'front')) {
            const px = c + x * r, py = c - y * r;
            if (pen) ctx.lineTo(px, py); else ctx.moveTo(px, py);
            pen = true;
          } else {
            pen = false;
          }
        }
        ctx.strokeStyle = item.kind === 'equator' ? accent : line;
        ctx.globalAlpha = pass === 'front' ? (item.kind === 'equator' ? 0.9 : 0.55 + speed * 0.3) : 0.12;
        ctx.lineWidth = (item.kind === 'equator' ? 2 : 1.2) * this.ratio;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // The pole marker: where the ball's own "up" is now.
    const [px, py, pd] = project(rotate(this.q, [0, 0, 1]));
    ctx.fillStyle = accent;
    ctx.globalAlpha = pd < 0 ? 1 : 0.25;
    ctx.beginPath();
    ctx.arc(c + px * r, c - py * r, 4.5 * this.ratio, 0, TAU);
    ctx.fill();
    ctx.globalAlpha = 1;

    // Rim highlight that brightens with speed.
    ctx.strokeStyle = accent;
    ctx.globalAlpha = 0.15 + speed * 0.6;
    ctx.lineWidth = 2 * this.ratio;
    ctx.beginPath();
    ctx.arc(c, c, r + 3 * this.ratio, 0, TAU);
    ctx.stroke();
    ctx.globalAlpha = 1;

    // Housing axes, fixed to the desk, as a small gizmo in the corner.
    const axes = [['X', [1, 0, 0], '--axis-x'], ['Y', [0, 1, 0], '--axis-y'], ['Z', [0, 0, 1], '--axis-z']];
    const ox = s * 0.11, oy = s * 0.89, len = s * 0.075;
    ctx.font = `600 ${10 * this.ratio}px ${style.fontFamily}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineCap = 'round';
    for (const [name, v, variable] of axes) {
      const [x, y] = project(v);
      ctx.strokeStyle = ctx.fillStyle = color(variable);
      ctx.lineWidth = 2 * this.ratio;
      ctx.beginPath();
      ctx.moveTo(ox, oy);
      ctx.lineTo(ox + x * len, oy - y * len);
      ctx.stroke();
      ctx.fillText(name, ox + x * len * 1.55, oy - y * len * 1.55);
    }
  }
}
