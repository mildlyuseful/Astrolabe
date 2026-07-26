/* SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Ball motion views.
 *
 * cube_test.py integrates the firmware's (rx, ry, rz) radian deltas as an axis-angle increment
 * into a running quaternion, so a cube spins 1:1 with the ball. These views do the same thing to
 * a sphere: one large orbit sphere for the live state panel, one small sphere in Global →
 * Physical transform that shows where the logical axes currently point.
 *
 * No device is attached in this demo, so motion comes from a gentle synthetic stream plus direct
 * dragging: inside the limb rotates the ball, the outer ring twists it.
 */

/* -------------------------------------------------------------- quaternions */

const quat = {
  identity: () => [1, 0, 0, 0],
  fromAxisAngle(ax, ay, az, angle) {
    const h = angle / 2, s = Math.sin(h);
    return [Math.cos(h), ax * s, ay * s, az * s];
  },
  mul(a, b) {
    return [
      a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
      a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
      a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
      a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ];
  },
  normalize(q) {
    const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
    return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
  },
  /* rotate a vector by q */
  apply(q, v) {
    const [w, x, y, z] = q;
    const [vx, vy, vz] = v;
    const tx = 2 * (y * vz - z * vy);
    const ty = 2 * (z * vx - x * vz);
    const tz = 2 * (x * vy - y * vx);
    return [
      vx + w * tx + (y * tz - z * ty),
      vy + w * ty + (z * tx - x * tz),
      vz + w * tz + (x * ty - y * tx),
    ];
  },
  /* integrate one rotation-vector increment expressed in camera space */
  integrate(q, rx, ry, rz) {
    const angle = Math.hypot(rx, ry, rz);
    if (angle < 1e-9) return q;
    const dq = quat.fromAxisAngle(rx / angle, ry / angle, rz / angle, angle);
    return quat.normalize(quat.mul(dq, q));
  },
};

/* --------------------------------------------------------- shared ball state */

const motion = {
  q: quat.identity(),
  rate: [0, 0, 0],       // smoothed rad/s, as the daemon would report packet deltas
  simulate: true,
  dragging: false,
  seed: Math.random() * 100,
  wheel: 0,              // accumulated wheel notches, pointer mode
  cursor: [0, 0],        // synthetic cursor offset, pointer mode
  trail: [],

  recenter() {
    this.q = quat.identity();
    this.rate = [0, 0, 0];
    this.wheel = 0;
    this.cursor = [0, 0];
    this.trail = [];
  },

  /* one frame of synthetic ball movement, shaped to look like a hand on a trackball */
  step(dt, opts) {
    let rx = 0, ry = 0, rz = 0;
    if (this.pending) {
      [rx, ry, rz] = this.pending;
      this.pending = null;
    } else if (this.simulate && !this.dragging) {
      const t = performance.now() / 1000 + this.seed;
      const swell = 0.5 + 0.5 * Math.sin(t * 0.21);
      rx = 0.34 * swell * (Math.sin(t * 0.53) + 0.42 * Math.sin(t * 1.27 + 1.1)) * dt;
      ry = 0.34 * swell * (Math.cos(t * 0.41) + 0.38 * Math.sin(t * 0.97 + 0.4)) * dt;
      rz = 0.20 * Math.sin(t * 0.29) * Math.sin(t * 0.13) * dt;
    }
    this.q = quat.integrate(this.q, rx, ry, rz);

    const k = 1 - Math.exp(-dt * 7);
    this.rate[0] += (rx / Math.max(dt, 1e-4) - this.rate[0]) * k;
    this.rate[1] += (ry / Math.max(dt, 1e-4) - this.rate[1]) * k;
    this.rate[2] += (rz / Math.max(dt, 1e-4) - this.rate[2]) * k;

    /* pointer-mode routing, gated exactly like the daemon's scroll dominance rule */
    if (opts && opts.mode === "pointer") {
      const planar = Math.hypot(rx, ry);
      const twist = Math.abs(rz);
      if (twist > opts.deadzone && twist > opts.dominance * planar) {
        this.wheel += rz * opts.scrollGain;
      } else {
        this.cursor[0] += ry * opts.cursorGain * 0.06;
        this.cursor[1] += rx * opts.cursorGain * 0.06;
        this.cursor[0] = Math.max(-1, Math.min(1, this.cursor[0]));
        this.cursor[1] = Math.max(-1, Math.min(1, this.cursor[1]));
        this.trail.push([this.cursor[0], this.cursor[1]]);
        if (this.trail.length > 26) this.trail.shift();
      }
    }
    return [rx, ry, rz];
  },

  push(rx, ry, rz) { this.pending = [rx, ry, rz]; },
};

/* ------------------------------------------------------------------- palette */

let palette = null;
function readPalette() {
  const cs = getComputedStyle(document.documentElement);
  const get = (name) => cs.getPropertyValue(name).trim();
  palette = {
    ink: get("--ink"), muted: get("--muted"), faint: get("--faint"),
    accent: get("--accent"), rule: get("--rule-strong"), warn: get("--warn"),
    surface: get("--surface-2"), sunk: get("--sunk"), ok: get("--ok"),
  };
  return palette;
}
function pal() { return palette || readPalette(); }

/* canvas colours must be plain rgba(), so resolve the hex tokens once */
const rgbCache = new Map();
function toRgb(color) {
  if (rgbCache.has(color)) return rgbCache.get(color);
  let out = [0, 0, 0];
  const hex = color.replace("#", "");
  if (/^[0-9a-f]{6}$/i.test(hex)) {
    out = [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
  } else if (/^[0-9a-f]{3}$/i.test(hex)) {
    out = [0, 1, 2].map((i) => parseInt(hex[i] + hex[i], 16));
  } else {
    const match = color.match(/(\d+(?:\.\d+)?)/g);
    if (match && match.length >= 3) out = match.slice(0, 3).map(Number);
  }
  rgbCache.set(color, out);
  return out;
}
function alphaColor(color, alpha) {
  const [r, g, b] = toRgb(color);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/* --------------------------------------------------------------- sphere view */

const LATS = [-60, -30, 0, 30, 60];
const LONS = [0, 30, 60, 90, 120, 150];
const D2R = Math.PI / 180;

function sphereLines() {
  const lines = [];
  LATS.forEach((lat) => {
    const pts = [];
    const cl = Math.cos(lat * D2R), sl = Math.sin(lat * D2R);
    for (let lon = 0; lon <= 360; lon += 7.5) {
      pts.push([cl * Math.cos(lon * D2R), sl, cl * Math.sin(lon * D2R)]);
    }
    lines.push({ pts, weight: lat === 0 ? 1.25 : 0.85 });
  });
  LONS.forEach((lon) => {
    const pts = [];
    const cx = Math.cos(lon * D2R), sz = Math.sin(lon * D2R);
    for (let lat = -90; lat <= 90; lat += 5) {
      const cl = Math.cos(lat * D2R);
      pts.push([cl * cx, Math.sin(lat * D2R), cl * sz]);
    }
    lines.push({ pts, weight: lon === 0 ? 1.15 : 0.85 });
  });
  return lines;
}
const GRID = sphereLines();

class BallView {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.variant = options.variant || "orbit";
    this.getContext = options.getContext || (() => ({}));
    this.size = options.size || 200;
    this.canvas.style.width = `${this.size}px`;
    this.canvas.style.height = `${this.size}px`;
    this.resize();
    this.bindDrag();
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = this.size * dpr;
    this.canvas.height = this.size * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  bindDrag() {
    const c = this.canvas;
    let last = null, twisting = false;
    const center = () => [this.size / 2, this.size / 2];
    const radius = () => this.size * 0.4;

    c.addEventListener("pointerdown", (e) => {
      const rect = c.getBoundingClientRect();
      const [cx, cy] = center();
      const px = e.clientX - rect.left - cx, py = e.clientY - rect.top - cy;
      twisting = Math.hypot(px, py) > radius() * 0.82 || e.shiftKey;
      last = [px, py];
      motion.dragging = true;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!last) return;
      const rect = c.getBoundingClientRect();
      const [cx, cy] = center();
      const px = e.clientX - rect.left - cx, py = e.clientY - rect.top - cy;
      if (twisting) {
        const a0 = Math.atan2(last[1], last[0]);
        const a1 = Math.atan2(py, px);
        let d = a1 - a0;
        if (d > Math.PI) d -= 2 * Math.PI;
        if (d < -Math.PI) d += 2 * Math.PI;
        motion.push(0, 0, -d);
      } else {
        const k = 1 / radius();
        motion.push((py - last[1]) * k, (px - last[0]) * k, 0);
      }
      last = [px, py];
    });
    const end = () => { last = null; motion.dragging = false; };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("lostpointercapture", end);
  }

  draw() {
    const ctx = this.ctx;
    const p = pal();
    const s = this.size;
    const cx = s / 2, cy = s / 2, R = s * 0.4;
    const info = this.getContext();
    ctx.clearRect(0, 0, s, s);

    /* body */
    const grad = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.1, cx, cy, R * 1.05);
    grad.addColorStop(0, alphaColor(p.surface, 1));
    grad.addColorStop(1, alphaColor(p.sunk, 1));
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    /* graticule, back half first so the front reads as nearer */
    const q = motion.q;
    const project = (v) => {
      const r = quat.apply(q, v);
      return [cx + r[0] * R, cy - r[1] * R, r[2]];
    };
    [false, true].forEach((front) => {
      GRID.forEach((line) => {
        ctx.beginPath();
        let drawing = false;
        for (let i = 0; i < line.pts.length; i++) {
          const [x, y, z] = project(line.pts[i]);
          const isFront = z >= 0;
          if (isFront !== front) { drawing = false; continue; }
          if (!drawing) { ctx.moveTo(x, y); drawing = true; } else { ctx.lineTo(x, y); }
        }
        ctx.strokeStyle = alphaColor(front ? p.ink : p.muted, front ? 0.3 : 0.1);
        ctx.lineWidth = line.weight * (front ? 1 : 0.8);
        ctx.stroke();
      });
    });

    /* limb */
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.strokeStyle = alphaColor(p.ink, 0.4);
    ctx.lineWidth = 1;
    ctx.stroke();

    /* twist ring: the outer band that drags rz */
    const twist = motion.rate[2];
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.09, 0, Math.PI * 2);
    ctx.strokeStyle = alphaColor(p.rule, 0.55);
    ctx.setLineDash([2, 4]);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);
    if (Math.abs(twist) > 0.02) {
      const span = Math.max(0.12, Math.min(1.5, Math.abs(twist) * 1.6));
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.09, -Math.PI / 2, -Math.PI / 2 + span * Math.sign(twist), twist < 0);
      ctx.strokeStyle = alphaColor(p.accent, 0.85);
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    if (this.variant === "axes") this.drawAxes(ctx, cx, cy, R, info);
    else this.drawOrbit(ctx, cx, cy, R, info);
  }

  /* main sphere: a fiducial dot so rotation is legible, plus the mode's motion cue */
  drawOrbit(ctx, cx, cy, R, info) {
    const p = pal();
    const marks = [
      [[0, 1, 0], p.accent, 3.1],     // north pole
      [[1, 0, 0], p.ink, 2.4],        // seam
      [[0, 0, 1], p.muted, 2.1],      // twist reference
    ];
    marks.forEach(([v, color, r]) => {
      const rv = quat.apply(motion.q, v);
      const x = cx + rv[0] * R, y = cy - rv[1] * R;
      const front = rv[2] >= 0;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = alphaColor(color, front ? 0.95 : 0.22);
      ctx.fill();
    });

    if (info.mode === "pointer") {
      /* pointer mode: planar motion becomes a cursor, dominant twist becomes wheel notches */
      const px = cx + motion.cursor[0] * R * 0.72;
      const py = cy + motion.cursor[1] * R * 0.72;
      ctx.beginPath();
      motion.trail.forEach(([tx, ty], i) => {
        const x = cx + tx * R * 0.72, y = cy + ty * R * 0.72;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = alphaColor(p.accent, 0.35);
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.save();
      ctx.translate(px, py);
      ctx.beginPath();
      ctx.moveTo(0, 0); ctx.lineTo(0, 11); ctx.lineTo(3.2, 7.6); ctx.lineTo(7.4, 7.2); ctx.closePath();
      ctx.fillStyle = p.ink;
      ctx.strokeStyle = alphaColor(p.surface, 0.9);
      ctx.lineWidth = 1;
      ctx.fill(); ctx.stroke();
      ctx.restore();

      const notches = Math.round(motion.wheel);
      if (notches !== 0) {
        ctx.font = "600 9px ui-monospace, monospace";
        ctx.fillStyle = p.accent;
        ctx.textAlign = "right";
        ctx.fillText(`${notches > 0 ? "+" : ""}${notches} wheel`, this.size - 3, 11);
      }
    } else {
      /* 3D mode: name what the current layer does with planar motion */
      const rate = motion.rate;
      const mag = Math.hypot(rate[0], rate[1]);
      if (mag > 0.03) {
        const ang = Math.atan2(-rate[0], rate[1]);
        const len = Math.min(R * 0.62, R * 0.22 + mag * 30);
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-ang);
        ctx.beginPath();
        ctx.moveTo(0, 0); ctx.lineTo(len, 0);
        ctx.strokeStyle = alphaColor(p.accent, 0.55);
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(len, 0); ctx.lineTo(len - 5, -3.2); ctx.lineTo(len - 5, 3.2); ctx.closePath();
        ctx.fillStyle = alphaColor(p.accent, 0.8);
        ctx.fill();
        ctx.restore();
      }
    }
  }

  /* transform sphere: three needles showing where each logical axis currently points */
  drawAxes(ctx, cx, cy, R, info) {
    const p = pal();
    const map = info.orientation || [[0, false], [1, false], [2, false]];
    const basis = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const colors = [p.accent, p.warn, p.muted];
    const names = ["X", "Y", "Z"];

    map.forEach(([source, invert], logical) => {
      const sign = invert ? -1 : 1;
      const v = basis[source].map((c) => c * sign);
      const rv = quat.apply(motion.q, v);
      const x = cx + rv[0] * R * 0.94, y = cy - rv[1] * R * 0.94;
      const front = rv[2] >= 0;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x, y);
      ctx.strokeStyle = alphaColor(colors[logical], front ? 0.9 : 0.25);
      ctx.lineWidth = front ? 1.6 : 1;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, 2.6, 0, Math.PI * 2);
      ctx.fillStyle = alphaColor(colors[logical], front ? 1 : 0.3);
      ctx.fill();
      ctx.font = "600 9.5px ui-monospace, monospace";
      ctx.fillStyle = alphaColor(colors[logical], front ? 1 : 0.35);
      ctx.textAlign = "center";
      const ox = (x - cx) * 0.16, oy = (y - cy) * 0.16;
      ctx.fillText(names[logical], x + ox, y + oy + 3);
    });
  }
}

/* one animation loop drives every mounted view */
const views = new Set();
let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  if (views.size && !document.hidden) {
    const first = views.values().next().value;
    motion.step(dt, first.getContext());
    views.forEach((view) => view.draw());
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function mountBall(canvas, options) {
  const view = new BallView(canvas, options);
  views.add(view);
  view.draw();
  return view;
}
function unmountBalls() { views.clear(); }
