// Fusion-style move manipulator: three arrows (translate) and three rings (rotate),
// drawn at a constant screen size, oriented to a frame. Dragging reports values in
// that frame: distance along an axis, or degrees about it.
import * as THREE from "three";
import { css } from "./api.js";

const col = (n) => new THREE.Color(css(n) || "#888");
const AXES = [["x", "--x"], ["y", "--y"], ["z", "--z"]];

export class Gizmo {
  constructor(vp, { onDrag, onStart, onEnd }) {
    this.vp = vp; this.onDrag = onDrag; this.onStart = onStart; this.onEnd = onEnd;
    this.root = new THREE.Group(); this.root.renderOrder = 20;
    this.handles = [];
    this.frame = null; this.drag = null; this.hot = null;
    this.build();
    vp.scene.add(this.root);
    this.root.visible = false;
    const c = vp.canvas;
    this._down = (e) => this.pointerDown(e);
    this._move = (e) => this.pointerMove(e);
    this._up = (e) => this.pointerUp(e);
    c.addEventListener("pointerdown", this._down, true);   // capture: before the orbit controls
    window.addEventListener("pointermove", this._move, true);
    window.addEventListener("pointerup", this._up, true);
    this._tick = () => { if (this.root.visible) this.rescale(); this.raf = requestAnimationFrame(this._tick); };
    this._tick();
  }

  dispose() {
    const c = this.vp.canvas;
    c.removeEventListener("pointerdown", this._down, true);
    window.removeEventListener("pointermove", this._move, true);
    window.removeEventListener("pointerup", this._up, true);
    cancelAnimationFrame(this.raf);
    this.vp.scene.remove(this.root);
    this.vp.controls.enabled = true;
  }

  build() {
    const mat = (c) => new THREE.MeshBasicMaterial({ color: col(c), depthTest: false, transparent: true, opacity: 0.95 });
    const pickMat = new THREE.MeshBasicMaterial({ visible: false });
    for (const [axis, c] of AXES) {
      // arrow: shaft + cone, pointing along +axis in a unit-size group
      const g = new THREE.Group();
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.8, 8), mat(c));
      shaft.position.y = 0.4 + 0.15;
      const head = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.22, 16), mat(c));
      head.position.y = 1.06;
      const pick = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.95, 8), pickMat);
      pick.position.y = 0.72;
      g.add(shaft, head, pick);
      if (axis === "x") g.rotation.z = -Math.PI / 2;
      if (axis === "z") g.rotation.x = Math.PI / 2;
      pick.userData = { kind: "move", axis, parts: [shaft, head], color: c };
      this.handles.push(pick); this.root.add(g);
      // ring: rotation about this axis, in the plane perpendicular to it
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.62, 0.012, 6, 64), mat(c));
      const rpick = new THREE.Mesh(new THREE.TorusGeometry(0.62, 0.07, 6, 48), pickMat);
      const rg = new THREE.Group(); rg.add(ring, rpick);
      if (axis === "x") rg.rotation.y = Math.PI / 2;
      if (axis === "y") rg.rotation.x = Math.PI / 2;
      rpick.userData = { kind: "rotate", axis, parts: [ring], color: c };
      this.handles.push(rpick); this.root.add(rg);
    }
    this.root.traverse((o) => { o.renderOrder = 20; });
  }

  // frame: {o, x, y, z} arrays
  show(frame) {
    this.frame = frame;
    const m = new THREE.Matrix4().makeBasis(new THREE.Vector3(...frame.x), new THREE.Vector3(...frame.y), new THREE.Vector3(...frame.z));
    this.root.quaternion.setFromRotationMatrix(m);
    this.root.position.set(...frame.o);
    this.root.visible = true;
    this.rescale();
  }

  hide() { this.root.visible = false; }

  moveTo(o) { this.root.position.set(...o); }

  rescale() {
    const d = this.vp.camera.position.distanceTo(this.root.position);
    const s = d * Math.tan(THREE.MathUtils.degToRad(this.vp.camera.fov / 2)) * 0.28;
    this.root.scale.setScalar(s);
  }

  axisDir(axis) { return new THREE.Vector3(...this.frame[axis]).normalize(); }

  hit(e) {
    if (!this.root.visible) return null;
    this.vp.ray.setFromCamera(this.vp.ndc(e), this.vp.camera);
    const h = this.vp.ray.intersectObjects(this.handles, false);
    return h.length ? h[0].object.userData : null;
  }

  highlight(u) {
    for (const h of this.handles) for (const p of h.userData.parts) p.material.color.copy(col(h.userData === u ? "--accent" : h.userData.color));
  }

  // value along the axis line closest to the mouse ray
  axisParam(e, o, d) {
    this.vp.ray.setFromCamera(this.vp.ndc(e), this.vp.camera);
    const r = this.vp.ray.ray;
    const w0 = o.clone().sub(r.origin);
    const b = d.dot(r.direction), dd = d.dot(w0), ee = r.direction.dot(w0);
    const den = 1 - b * b;
    if (Math.abs(den) < 1e-6) return null;
    return (b * ee - dd) / den;
  }

  ringAngle(e, o, d, u0) {
    this.vp.ray.setFromCamera(this.vp.ndc(e), this.vp.camera);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(d, o);
    const p = new THREE.Vector3();
    if (!this.vp.ray.ray.intersectPlane(plane, p)) return null;
    const v = p.sub(o);
    if (!u0) return v.normalize();
    return Math.atan2(new THREE.Vector3().crossVectors(u0, v).dot(d), u0.dot(v)) * 180 / Math.PI;
  }

  pointerDown(e) {
    if (e.button !== 0) return;
    const u = this.hit(e);
    if (!u) return;
    e.stopPropagation(); e.preventDefault();
    this.vp.controls.enabled = false;
    const o = this.root.position.clone(), d = this.axisDir(u.axis);
    this.drag = { u, o, d, start: u.kind === "move" ? this.axisParam(e, o, d) : this.ringAngle(e, o, d, null) };
    if (this.drag.start === null) { this.drag = null; this.vp.controls.enabled = true; return; }
    this.onStart && this.onStart(u.kind, u.axis);
  }

  pointerMove(e) {
    if (!this.drag) {
      const u = this.hit(e);
      if (u !== this.hot) { this.hot = u; this.highlight(u); this.vp.canvas.style.cursor = u ? "grab" : ""; }
      return;
    }
    e.stopPropagation();
    const { u, o, d, start } = this.drag;
    let v;
    if (u.kind === "move") {
      const t = this.axisParam(e, o, d); if (t === null) return;
      const step = e.shiftKey ? 0.1 : (this.vp.gridStep >= 10 ? 1 : 0.5);
      v = Math.round((t - start) / step) * step;
    } else {
      const a = this.ringAngle(e, o, d, start); if (a === null) return;
      const step = e.shiftKey ? 1 : 5;
      v = Math.round(a / step) * step;
    }
    this.onDrag(u.kind, u.axis, v);
  }

  pointerUp(e) {
    if (!this.drag) return;
    e.stopPropagation();
    this.drag = null; this.vp.controls.enabled = true;
    this.onEnd && this.onEnd();
  }
}
