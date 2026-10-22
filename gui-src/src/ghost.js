// Primitive previews drawn in the browser, plus Fusion-style size arrows.
// Nothing here touches the model: the ghost is drawn from the dialog's values, and dragging an
// arrow only edits those values. The script gets one statement, with the final sizes, on OK.
import * as THREE from "three";
import { css } from "./api.js";

const col = (n) => new THREE.Color(css(n) || "#888");
const V = (a) => new THREE.Vector3(...a);

// The frame the engine builds in: spin about Z, and a negative height turns the frame over
// (180 degrees about X). A corner-based box is shifted so its footprint stays put.
// Mirrors ops._creation_frame exactly.
export function buildFrame(base, spin, height, shiftY) {
  const a = (spin || 0) * Math.PI / 180;
  const x0 = V(base.x), y0 = V(base.y), z = V(base.z), o = V(base.o);
  const x = x0.clone().multiplyScalar(Math.cos(a)).addScaledVector(y0, Math.sin(a));
  const y = z.clone().cross(x);
  if (height !== undefined && height < 0) {
    return { o: o.clone().addScaledVector(y, shiftY || 0), x, y: y.clone().negate(), z: z.clone().negate() };
  }
  return { o, x, y, z };
}

function frameMatrix(f) {
  return new THREE.Matrix4().makeBasis(f.x, f.y, f.z).setPosition(f.o);
}

// Local geometry of each primitive, in its own frame (Z = build direction).
function geometry(tool, v) {
  const H = Math.abs(v.h ?? 0);
  switch (tool) {
    case "box": {
      const g = new THREE.BoxGeometry(v.length, v.width, H);
      const off = { corner: [v.length / 2, v.width / 2, H / 2], center_xy: [0, 0, H / 2], center: [0, 0, 0] }[v.origin || "corner"];
      g.translate(...off);
      return g;
    }
    case "cylinder": case "cone": case "polygon": {
      const n = tool === "polygon" ? Math.max(3, Math.round(v.n || 6)) : 48;
      if (tool === "polygon") {
        const shape = new THREE.Shape();
        for (let i = 0; i < n; i++) { const t = i / n * Math.PI * 2; i ? shape.lineTo(v.r * Math.cos(t), v.r * Math.sin(t)) : shape.moveTo(v.r, 0); }
        shape.closePath();
        return new THREE.ExtrudeGeometry(shape, { depth: H, bevelEnabled: false });
      }
      const top = tool === "cone" ? v.r2 : v.r, bottom = tool === "cone" ? v.r1 : v.r;
      const g = new THREE.CylinderGeometry(Math.max(top, 1e-3), Math.max(bottom, 1e-3), H, n);
      g.rotateX(Math.PI / 2); g.translate(0, 0, H / 2);
      return g;
    }
    case "sphere": return new THREE.SphereGeometry(v.r, 40, 24);
    case "profile_rect": {
      const c = (v.origin || "corner") === "corner";
      const g = new THREE.PlaneGeometry(v.length, v.width);
      if (c) g.translate(v.length / 2, v.width / 2, 0);
      return g;
    }
    case "profile_circle": return new THREE.CircleGeometry(v.r, 64);
    case "torus": return new THREE.TorusGeometry(v.R, Math.min(v.r, v.R * 0.98), 20, 64);
    case "coil": {
      const turns = Math.max(v.turns || 1, 0.1), s = v.hand === "left" ? -1 : 1, P = Math.abs(v.pitch || 1);
      const curve = new THREE.Curve();
      curve.getPoint = (t, out = new THREE.Vector3()) => {
        const a = s * t * turns * Math.PI * 2;
        return out.set(v.R * Math.cos(a), v.R * Math.sin(a), t * turns * P);
      };
      return new THREE.TubeGeometry(curve, Math.round(48 * turns), Math.max((v.size || 1) / 2, 0.05), 12, false);
    }
  }
  return null;
}

// Arrow handles per primitive, in the *unflipped* frame (so a height arrow can pass through zero).
// key: field it edits; p: local position; d: local direction; mul: field change per mm dragged.
function handles(tool, v) {
  const h = v.h ?? 0;
  switch (tool) {
    case "box": {
      const c = (v.origin || "corner") === "corner", cz = v.origin === "center";
      const x0 = c ? 0 : -v.length / 2, y0 = c ? 0 : -v.width / 2;
      const zc = cz ? 0 : h / 2;
      return [
        { key: "length", p: [x0 + v.length, y0 + v.width / 2, zc], d: [1, 0, 0], mul: c ? 1 : 2, min: 1e-3 },
        { key: "width", p: [x0 + v.length / 2, y0 + v.width, zc], d: [0, 1, 0], mul: c ? 1 : 2, min: 1e-3 },
        { key: "height", p: [x0 + v.length / 2, y0 + v.width / 2, cz ? h / 2 : h], d: [0, 0, 1], mul: cz ? 2 : 1, signed: true },
      ];
    }
    case "cylinder": return [{ key: "r", p: [v.r, 0, h / 2], d: [1, 0, 0], mul: 1, min: 1e-3 }, { key: "h", p: [0, 0, h], d: [0, 0, 1], mul: 1, signed: true }];
    case "cone": return [{ key: "r1", p: [v.r1, 0, 0], d: [1, 0, 0], mul: 1, min: 0 }, { key: "r2", p: [v.r2, 0, h], d: [1, 0, 0], mul: 1, min: 0 },
      { key: "h", p: [0, 0, h], d: [0, 0, 1], mul: 1, signed: true }];
    case "polygon": return [{ key: "r", p: [v.r, 0, h / 2], d: [1, 0, 0], mul: 1, min: 1e-3 }, { key: "h", p: [0, 0, h], d: [0, 0, 1], mul: 1, signed: true }];
    case "sphere": return [{ key: "r", p: [v.r, 0, 0], d: [1, 0, 0], mul: 1, min: 1e-3 }];
    case "profile_rect": {
      const c = (v.origin || "corner") === "corner";
      const x0 = c ? 0 : -v.length / 2, y0 = c ? 0 : -v.width / 2;
      return [{ key: "length", p: [x0 + v.length, y0 + v.width / 2, 0], d: [1, 0, 0], mul: c ? 1 : 2, min: 1e-3 },
        { key: "width", p: [x0 + v.length / 2, y0 + v.width, 0], d: [0, 1, 0], mul: c ? 1 : 2, min: 1e-3 }];
    }
    case "profile_circle": return [{ key: "r", p: [v.r, 0, 0], d: [1, 0, 0], mul: 1, min: 1e-3 }];
    case "torus": return [{ key: "R", p: [v.R, 0, 0], d: [1, 0, 0], mul: 1, min: 1e-3 }, { key: "r", p: [v.R, 0, v.r], d: [0, 0, 1], mul: 1, min: 1e-3 }];
    case "coil": {
      const turns = Math.max(v.turns || 1, 0.1), H = turns * (v.pitch || 0);
      return [{ key: "R", p: [v.R, 0, 0], d: [1, 0, 0], mul: 1, min: 1e-3 }, { key: "pitch", p: [0, 0, H], d: [0, 0, 1], mul: 1 / turns, signed: true }];
    }
  }
  return [];
}

export class Ghost {
  // onChange(key, value) is called while an arrow is dragged.
  constructor(vp, onChange) {
    this.vp = vp; this.onChange = onChange;
    this.group = new THREE.Group(); vp.scene.add(this.group);
    this.arrows = new THREE.Group(); vp.scene.add(this.arrows);
    this.list = []; this.drag = null; this.hot = null;
    this._down = (e) => this.pointerDown(e);
    this._move = (e) => this.pointerMove(e);
    this._up = (e) => this.pointerUp(e);
    vp.canvas.addEventListener("pointerdown", this._down, true);
    window.addEventListener("pointermove", this._move, true);
    window.addEventListener("pointerup", this._up, true);
    this._tick = () => { this.rescale(); this.raf = requestAnimationFrame(this._tick); };
    this._tick();
  }

  dispose() {
    this.vp.canvas.removeEventListener("pointerdown", this._down, true);
    window.removeEventListener("pointermove", this._move, true);
    window.removeEventListener("pointerup", this._up, true);
    cancelAnimationFrame(this.raf);
    this.vp.scene.remove(this.group); this.vp.scene.remove(this.arrows);
    this.vp.controls.enabled = true;
  }

  get active() { return !!this.drag; }

  // tool: primitive id; v: numeric values incl. spin, origin; base: {o,x,y,z} frame (arrays)
  update(tool, v, base, withArrows = true) {
    this.group.clear();
    this.arrows.clear(); this.list = [];
    if (!base) return;
    const hKey = { box: "height", cylinder: "h", cone: "h", polygon: "h", coil: "pitch" }[tool];
    const hv = hKey ? v[hKey] : undefined;
    const vv = { ...v, h: tool === "box" ? v.height : tool === "coil" ? (v.turns || 1) * (v.pitch || 0) : v.h };
    const ok = Object.entries(vv).every(([k, x]) => typeof x !== "number" || Number.isFinite(x));
    const unflipped = buildFrame(base, v.spin, undefined);
    const shift = tool === "box" && (v.origin || "corner") === "corner" ? v.width : 0;
    const built = buildFrame(base, v.spin, hv, shift);
    const g = ok ? geometry(tool, { ...vv, h: Math.abs(vv.h || 0) }) : null;
    if (g && (tool === "sphere" || tool === "torus" || tool.startsWith("profile") || Math.abs(vv.h || 0) > 1e-9)) {
      const m = frameMatrix(built);
      const fill = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: col("--accent"), transparent: true, opacity: 0.3,
        depthWrite: false, side: THREE.DoubleSide, roughness: 0.7 }));
      fill.applyMatrix4(m); fill.renderOrder = 5;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 25), new THREE.LineBasicMaterial({ color: col("--accent"), transparent: true, opacity: 0.9, depthTest: false }));
      edges.applyMatrix4(m); edges.renderOrder = 6;
      this.group.add(fill, edges);
    }
    if (!withArrows) return;
    // arrows
    const m0 = frameMatrix(unflipped);
    const rot = new THREE.Matrix4().extractRotation(m0);
    for (const hd of handles(tool, vv)) {
      if (hd.p.some((c) => !Number.isFinite(c))) continue;
      const p = V(hd.p).applyMatrix4(m0), d = V(hd.d).applyMatrix4(rot).normalize();
      if (hd.signed && (vv.h || 0) < 0) d.negate();
      const arrow = this.arrow(p, d);
      Object.assign(arrow.userData, hd, { wp: p, wd: hd.signed && (vv.h || 0) < 0 ? d.clone().negate() : d, value: hd.key === "pitch" ? v.pitch : v[hd.key] });
      this.arrows.add(arrow); this.list.push(arrow);
    }
    this.rescale();
  }

  arrow(p, d) {
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ color: col("--sel"), depthTest: false });
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.55, 8), mat); shaft.position.y = 0.32;
    const head = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.26, 16), mat); head.position.y = 0.7;
    const hit = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.95, 8), new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.y = 0.5;
    g.add(shaft, head, hit);
    g.position.copy(p);
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d);
    g.traverse((o) => { o.renderOrder = 22; });
    g.userData.parts = [shaft, head]; g.userData.hit = hit;
    return g;
  }

  rescale() {
    for (const a of this.list) {
      const dist = this.vp.camera.position.distanceTo(a.position);
      a.scale.setScalar(dist * Math.tan(THREE.MathUtils.degToRad(this.vp.camera.fov / 2)) * 0.16);
    }
  }

  hit(e) {
    if (!this.list.length) return null;
    this.vp.ray.setFromCamera(this.vp.ndc(e), this.vp.camera);
    const hits = this.vp.ray.intersectObjects(this.list.map((a) => a.userData.hit), false);
    return hits.length ? this.list.find((a) => a.userData.hit === hits[0].object) : null;
  }

  axisParam(e, o, d) {
    this.vp.ray.setFromCamera(this.vp.ndc(e), this.vp.camera);
    const r = this.vp.ray.ray, w0 = o.clone().sub(r.origin);
    const b = d.dot(r.direction), dd = d.dot(w0), ee = r.direction.dot(w0), den = 1 - b * b;
    return Math.abs(den) < 1e-6 ? null : (b * ee - dd) / den;
  }

  pointerDown(e) {
    if (e.button !== 0) return;
    const a = this.hit(e);
    if (!a) return;
    e.stopPropagation(); e.preventDefault();
    this.vp.controls.enabled = false;
    const u = a.userData;
    const t0 = this.axisParam(e, u.wp, u.wd);
    if (t0 === null) { this.vp.controls.enabled = true; return; }
    this.drag = { u, t0, v0: u.value, dir: u.wd.clone(), origin: u.wp.clone() };
  }

  pointerMove(e) {
    if (!this.drag) {
      const a = this.hit(e);
      if (a !== this.hot) {
        for (const x of this.list) for (const p of x.userData.parts) p.material.color.copy(col(x === a ? "--accent" : "--sel"));
        this.hot = a; this.vp.canvas.style.cursor = a ? "grab" : "";
      }
      return !!a;
    }
    e.stopPropagation();
    const { u, t0, v0, dir, origin } = this.drag;
    const t = this.axisParam(e, origin, dir);
    if (t === null) return true;
    const step = e.shiftKey ? 0.1 : this.vp.snapStep();
    // the drag axis is the value's positive direction (even when a negative height's arrow points down)
    let val = v0 + (t - t0) * u.mul;
    val = Math.round(val / step) * step;
    if (u.min !== undefined && val < u.min) val = u.min > 0 ? Math.max(step, u.min) : 0;
    if (u.signed && Math.abs(val) < 1e-9) val = t - t0 >= 0 ? step : -step;
    this.onChange(u.key, Math.round(val * 1e6) / 1e6, e);
    return true;
  }

  pointerUp(e) {
    if (!this.drag) return;
    e.stopPropagation();
    this.drag = null; this.vp.controls.enabled = true;
  }
}
