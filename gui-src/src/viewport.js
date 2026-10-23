// The 3D viewport: scene building, body-first picking, snap points and helpers.
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { css, q } from "./api.js";

const col = (name) => new THREE.Color(css(name) || "#888");
const BODY_COLORS = ["--body-a", "--body-b", "--body-c"];
// highlighted edges are drawn as wide lines whose width is in screen pixels, so they look the
// same at any zoom (like the anchor dots)
export const EDGE_HOVER_PX = 5, EDGE_SELECTED_PX = 4;

export class Viewport {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.localClippingEnabled = true;           // the section view clips the bodies
    this.section = null; this.clip = null;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true; this.controls.dampingFactor = 0.14; this.controls.screenSpacePanning = true;
    // Fusion-style mouse: left drag is the selection box (main.js), right drag orbits, middle drag pans.
    // The wheel is ours (bindWheel): two-finger trackpad scroll pans, pinch and the mouse wheel zoom
    // at the cursor, Shift + two-finger scroll orbits.
    this.controls.mouseButtons = { LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE };
    this.controls.enableZoom = false;
    this.bindWheel();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.0));
    const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(0.4, -0.6, 1); this.camera.add(key);
    this.scene.add(this.camera);
    this.root = new THREE.Group(); this.scene.add(this.root);
    this.overlay = new THREE.Group(); this.scene.add(this.overlay);       // snap dots, triads, picks
    this.pointGroup = new THREE.Group(); this.scene.add(this.pointGroup); // a point shown from a list (View attributes)
    this.temp = new THREE.Group(); this.scene.add(this.temp);             // rubber bands, measure lines
    this.grid = null; this.localGrid = null; this.gridKey = ""; this.gridStep = 1;
    this.localFrame = null; this.localKey = ""; this.localStep = 0;
    this.axes = this.makeAxes(); this.scene.add(this.axes);
    this.show = { anchors: false, ghosts: false, grid: true, origin: false };
    this.originForced = false; this.originGroup = null; this.originKey = "";
    this.onFrame = []; this.anim = null;
    this.hidden = new Set();
    this.opacity = new Map();          // body id -> 0..1, set from the body menu (see-through to check walls)
    this.data = null; this.faceMeshes = []; this.edgeLines = []; this.knobs = [];
    this.highlight = { hover: null, selected: new Set(), focus: new Set() };
    this.modelSize = 100;
    this.ray = new THREE.Raycaster();
    new ResizeObserver(() => this.resize()).observe(canvas);
    const loop = (now) => {
      requestAnimationFrame(loop);
      if (this.anim) this.stepAnim(now);
      this.controls.update();
      this.updateGrid(); this.updateAxes(); this.updateLocalGrid(); this.updateOrigin(); this.updateScreenSized();
      this.renderer.render(this.scene, this.camera);
      for (const f of this.onFrame) f();
    };
    requestAnimationFrame(loop);
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height; this.camera.updateProjectionMatrix();
    for (const m of this._edgeHiMats || []) m.resolution.set(r.width, r.height);
  }

  // ---------------------------------------------------------------- building
  setScene(d, { keepCamera = true, focus = [] } = {}) {
    const first = !this.data;
    this.data = d;
    this.highlight.focus = new Set(focus);
    this.renderer.setClearColor(col("--view"));
    this.root.clear(); this.faceMeshes = []; this.edgeLines = []; this.knobs = []; this.groups = {};
    const box = new THREE.Box3();
    let ci = 0;
    for (const b of d.bodies) {
      const isRef = b.role === "reference", isGone = b.role === "removed";
      const colorIdx = ci++;
      if (isGone && !this.show.ghosts) continue;
      if (this.hidden.has(b.id)) continue;
      const g = new THREE.Group(); g.userData.body = b.id; this.groups[b.id] = g;
      const base = isRef ? col("--ref") : isGone ? col("--skip") : col(BODY_COLORS[colorIdx % 3]);
      for (const f of b.faces) {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.Float32BufferAttribute(f.p, 3));
        geo.setAttribute("normal", new THREE.Float32BufferAttribute(f.n, 3));
        geo.setIndex(f.i);
        const baseOpacity = isRef ? 0.3 : isGone ? 0.12 : 1, op = baseOpacity * (this.opacity.get(b.id) ?? 1);
        const mat = new THREE.MeshStandardMaterial({ color: base, roughness: 0.62, metalness: 0.04, side: THREE.DoubleSide,
          transparent: op < 1, opacity: op, depthWrite: op >= 1,
          polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
        mat.userData = { baseOpacity };
        const m = new THREE.Mesh(geo, mat);
        m.userData = { kind: "face", body: b.id, role: b.role, label: f.label, face: f, ref: `${b.id}.face(${q(f.label)})` };
        g.add(m); this.faceMeshes.push(m);
        geo.computeBoundingBox(); if (!isGone) box.union(geo.boundingBox);
      }
      if (!isGone) for (const e of b.edges) {
        if (e.label.startsWith("seam(")) continue;
        const pts = [];
        for (let i = 0; i + 3 < e.p.length; i += 3) pts.push(e.p[i], e.p[i + 1], e.p[i + 2], e.p[i + 3], e.p[i + 4], e.p[i + 5]);
        if (!pts.length) continue;
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
        const ln = e.construction       // a sketch's construction curve: dashed
          ? new THREE.LineSegments(geo, new THREE.LineDashedMaterial({ color: col("--ref"), transparent: true, opacity: 0.6, dashSize: this.modelSize * 0.015, gapSize: this.modelSize * 0.01 }))
          : new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: isRef ? col("--ref") : col("--edge"), transparent: true, opacity: isRef ? 0.6 : 0.85 }));
        if (e.construction) ln.computeLineDistances();
        ln.userData = { kind: "edge", body: b.id, label: e.label, ref: `${b.id}.edge(${q(e.label)})`, points: e.p };
        g.add(ln); this.edgeLines.push(ln);
      }
      this.root.add(g);
    }
    if (box.isEmpty()) box.set(new THREE.Vector3(-20, -20, 0), new THREE.Vector3(20, 20, 10));
    const size = box.getSize(new THREE.Vector3());
    this.modelSize = Math.max(size.x, size.y, size.z, 10);
    this.bbox = box;
    this.gridKey = "";
    this.drawAnchors();
    this.applyHighlights();
    this.applySection();
    if (first || !keepCamera) this.frame("iso");
  }

  // ---------------------------------------------------------------- origin planes
  // Fusion's Origin folder: the XY, XZ and YZ planes through the world origin, drawn as
  // see-through squares you can click to sketch on or to use as a plane or anchor. Shown with
  // the Origin toggle, and whenever a tool is asking for a plane or an anchor.
  originVisible() { return this.show.origin || this.originForced; }
  setOriginForced(on) { if (this.originForced !== !!on) { this.originForced = !!on; this.originKey = null; } }

  updateOrigin() {
    const vis = this.originVisible();
    const size = Math.max(20, Math.ceil(this.modelSize * 0.6 / 10) * 10);
    const key = vis ? `${size}` : "";
    if (key === this.originKey) return;
    this.originKey = key;
    if (this.originGroup) { this.scene.remove(this.originGroup); this.originGroup.traverse((o) => { o.geometry && o.geometry.dispose(); o.material && o.material.dispose(); }); this.originGroup = null; }
    if (!vis) return;
    const g = new THREE.Group();
    for (const [name, pl] of Object.entries(ORIGIN_PLANES)) {
      const x = new THREE.Vector3(...pl.x), z = new THREE.Vector3(...pl.z), y = z.clone().cross(x);
      const h = size / 2, corners = [[-h, -h], [h, -h], [h, h], [-h, h]].map(([a, b]) => x.clone().multiplyScalar(a).addScaledVector(y, b));
      const geo = new THREE.BufferGeometry().setFromPoints([corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]);
      geo.computeVertexNormals();
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: col(pl.color), transparent: true, opacity: 0.1, side: THREE.DoubleSide, depthWrite: false }));
      m.renderOrder = 3;
      m.userData = { kind: "origin", plane: name, ref: `world.anchor(${q(name)})`, frame: { o: [0, 0, 0], x: pl.x, y: y.toArray(), z: pl.z } };
      const edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(corners), new THREE.LineBasicMaterial({ color: col(pl.color), transparent: true, opacity: 0.6 }));
      edge.raycast = () => {}; edge.userData = { kind: "origin-edge", plane: name };
      g.add(m, edge);
    }
    this.originGroup = g; this.scene.add(g);
  }

  // The origin plane under the cursor, if it is nearer than any face there: {plane, ref, frame, point}
  pickOrigin(ev) {
    if (!this.originGroup) return null;
    this.ray.setFromCamera(this.ndc(ev), this.camera);
    const hs = this.ray.intersectObjects(this.originGroup.children.filter((o) => o.userData.kind === "origin"), false);
    if (!hs.length) return null;
    const faces = this.hits(ev, { edges: false });
    if (faces.length && faces[0].distance < hs[0].distance - this.worldPerPx()) return null;
    const u = hs[0].object.userData;
    return { plane: u.plane, ref: u.ref, frame: u.frame, point: hs[0].point.toArray() };
  }

  setOriginHover(name) {
    if (!this.originGroup) return;
    for (const o of this.originGroup.children) {
      if (o.userData.kind === "origin") o.material.opacity = o.userData.plane === name ? 0.32 : 0.1;
      else o.material.opacity = o.userData.plane === name ? 1 : 0.6;
    }
  }

  // ---------------------------------------------------------------- section view
  // A plane through the model (o, n): what lies in front of it (the side n points to) is cut
  // away, so the inside shows. The cut is filled with a flat colour: each solid face is drawn
  // again from behind in that colour, and only its back shows through the cut.
  setSection(sec) { this.section = sec; this.applySection(); }

  applySection() {
    for (const c of this._caps || []) { c.parent && c.parent.remove(c); c.material.dispose(); }
    this._caps = [];
    const sec = this.section;
    this.clip = sec ? new THREE.Plane().setFromNormalAndCoplanarPoint(new THREE.Vector3(...sec.n).normalize().negate(), new THREE.Vector3(...sec.o)) : null;
    const planes = this.clip ? [this.clip] : [];
    const cap = col("--section");
    for (const m of this.faceMeshes) {
      const front = THREE.FrontSide, back = THREE.BackSide;      // triangles wind outward (mirrored bodies too)
      m.material.clippingPlanes = planes;
      m.material.side = planes.length && m.userData.role === "solid" ? front : THREE.DoubleSide;
      m.material.needsUpdate = true;
      if (!planes.length || m.userData.role !== "solid") continue;
      const c = new THREE.Mesh(m.geometry, new THREE.MeshBasicMaterial({ color: cap, side: back, clippingPlanes: planes,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
      c.raycast = () => {}; c.userData = { kind: "cap" };
      m.parent.add(c); this._caps.push(c);
    }
    for (const l of this.edgeLines) { l.material.clippingPlanes = planes; l.material.needsUpdate = true; }
    for (const o of this._edgeHi || []) { o.material.clippingPlanes = planes; o.material.needsUpdate = true; }
  }

  // ---------------------------------------------------------------- grid that follows the zoom
  // Minor lines about 18 px apart, from a 1-2-5 series; a darker line every 5 or 10.
  updateGrid() {
    const wpp = this.worldPerPx();
    const want = wpp * 18;
    const p10 = Math.pow(10, Math.floor(Math.log10(want)));
    const m = want / p10;
    const mant = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
    const step = mant * p10;
    const major = step * (mant === 2 ? 5 : mant === 5 ? 2 : 10) ;
    const r = this.canvas.getBoundingClientRect();
    const camDist = this.camera.position.distanceTo(this.controls.target);
    const half = Math.max(Math.ceil(Math.max(r.width, r.height) * wpp * 1.4 / major), 2) * major;
    const t = this.controls.target;
    const cx = Math.round(t.x / major) * major, cy = Math.round(t.y / major) * major;
    const faceGrid = !!this.localFrame;      // one grid at a time: a face grid replaces the bed grid
    const key = `${step}|${cx}|${cy}|${half}|${this.show.grid}|${faceGrid}`;
    this.gridStep = step;
    if (key === this.gridKey) return;
    this.gridKey = key;
    if (this.grid) { this.scene.remove(this.grid); this.grid.traverse((o) => o.geometry && o.geometry.dispose()); this.grid = null; }
    if (!this.show.grid || faceGrid || half / step > 600) return;
    const minor = [], maj = [];
    const n = Math.round(half / step);
    for (let i = -n; i <= n; i++) {
      const v = i * step;
      const isMajor = Math.abs(Math.round((cx + v) / major) * major - (cx + v)) < step * 1e-3;
      const arr = isMajor ? maj : minor;
      arr.push(cx - half, cy + v, 0, cx + half, cy + v, 0, cx + v, cy - half, 0, cx + v, cy + half, 0);
    }
    const g = new THREE.Group();
    for (const [pts, c, o] of [[minor, "--grid-minor", 0.9], [maj, "--grid-major", 1]]) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      g.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: col(c), transparent: true, opacity: o, depthWrite: false })));
    }
    g.renderOrder = -1;
    this.grid = g; this.scene.add(g);
    this.gridMajor = major; this.gridHalf = half; void camDist;
  }

  // snapping step for clicked points: a 1-2-5 value about a quarter of a grid square
  // (1 mm on a 5 mm grid, 0.2 mm on a 1 mm grid)
  snapStep() {
    if (this.localFrame && this.localStep) return this.localStep;   // on a face: snap to its grid lines
    const want = (this.gridStep || 1) / 4;
    const p10 = Math.pow(10, Math.floor(Math.log10(want)));
    const m = want / p10;
    return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p10;
  }

  // ---------------------------------------------------------------- world origin and axes
  makeAxes() {
    const g = new THREE.Group();
    this.axisParts = [];
    for (const [d, c] of [[[1, 0, 0], "--x"], [[0, 1, 0], "--y"], [[0, 0, 1], "--z"]]) {
      for (const sign of [1, -1]) {
        const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 8, 1, true),
          new THREE.MeshBasicMaterial({ color: col(c), transparent: sign < 0, opacity: sign < 0 ? 0.35 : 1 }));
        m.userData = { d: new THREE.Vector3(...d).multiplyScalar(sign), color: c };
        m.raycast = () => {};
        g.add(m); this.axisParts.push(m);
      }
    }
    this.originDot = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), new THREE.MeshBasicMaterial({ color: col("--ink") }));
    this.originDot.raycast = () => {};
    g.add(this.originDot);
    this.axisLabels = [];
    for (const [d, c, txt] of [[[1, 0, 0], "--x", "X"], [[0, 1, 0], "--y", "Y"], [[0, 0, 1], "--z", "Z"]]) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: textSprite(txt, css(c)), depthTest: false, transparent: true }));
      s.userData.d = new THREE.Vector3(...d); s.renderOrder = 9;
      g.add(s); this.axisLabels.push(s);
    }
    return g;
  }

  updateAxes() {
    if (!this.axisParts) return;
    const o = new THREE.Vector3(0, 0, 0);
    const wpp = this.camera.position.distanceTo(o) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 2 / (this.canvas.getBoundingClientRect().height || 1);
    const len = Math.max(this.gridHalf || 50, wpp * 300);
    for (const m of this.axisParts) {
      const d = m.userData.d;
      const L = d.z < 0 ? len * 0.15 : len;          // little of the axis below the bed
      m.scale.set(wpp * 1.2, L, wpp * 1.2);
      m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d);
      m.position.copy(d.clone().multiplyScalar(L / 2));
    }
    this.originDot.scale.setScalar(wpp * 3.5);
    for (const s of this.axisLabels) {
      s.position.copy(s.userData.d.clone().multiplyScalar(wpp * 70));
      s.scale.set(wpp * 16, wpp * 16, 1);
    }
  }

  // A grid on a face's anchor frame (shown when a face is picked or a tool places on it), like
  // Fusion's sketch grid. It follows the zoom: lines about 12 px apart at the spot you are looking
  // at, so zooming in gives finer steps, and clicked points snap to its lines.
  showLocalGrid(frame) {
    this.localFrame = frame || null; this.localKey = ""; this.localStep = 0;
    if (!frame && this.localGrid) { this.scene.remove(this.localGrid); this.localGrid.traverse((o) => o.geometry && o.geometry.dispose()); this.localGrid = null; }
  }

  updateLocalGrid() {
    const fr = this.localFrame;
    if (!fr) return;
    const o = new THREE.Vector3(...fr.o), x = new THREE.Vector3(...fr.x), y = new THREE.Vector3(...fr.y);
    const z = new THREE.Vector3(...(fr.z || x.clone().cross(y).toArray()));
    // the spot on the plane in the middle of the view (or the frame origin if the plane is edge-on)
    this.ray.setFromCamera(new THREE.Vector2(0, 0), this.camera);
    const c = new THREE.Vector3();
    const hit = this.ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(z, o), c);
    const focus = hit && c.distanceTo(this.camera.position) < this.camera.position.distanceTo(o) * 6 ? c : o;
    const wpp = this.wppAt(focus);
    const want = wpp * 12;
    const p10 = Math.pow(10, Math.floor(Math.log10(want)));
    const m = want / p10;
    const mant = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
    const step = mant * p10, major = step * (mant === 2 ? 5 : mant === 5 ? 2 : 10);
    const r = this.canvas.getBoundingClientRect();
    const half = Math.ceil(Math.max(r.width, r.height) * wpp * 0.8 / major) * major;
    const rel = focus.clone().sub(o);
    const cu = Math.round(rel.dot(x) / major) * major, cv = Math.round(rel.dot(y) / major) * major;
    const key = `${step}|${cu}|${cv}|${half}`;
    this.localStep = step;
    if (key === this.localKey) return;
    this.localKey = key;
    if (this.localGrid) { this.scene.remove(this.localGrid); this.localGrid.traverse((q) => q.geometry && q.geometry.dispose()); }
    const n = Math.min(Math.round(half / step), 200);
    const buckets = { minor: [], major: [], axis: [] };
    const at = (u, v) => o.clone().addScaledVector(x, u).addScaledVector(y, v);
    for (let i = -n; i <= n; i++) {
      for (const [isU, base] of [[true, cu], [false, cv]]) {
        const t = base + i * step;
        const kind = Math.abs(t) < step * 1e-3 ? "axis" : Math.abs(Math.round(t / major) * major - t) < step * 1e-3 ? "major" : "minor";
        const a = isU ? at(t, cv - n * step) : at(cu - n * step, t), b = isU ? at(t, cv + n * step) : at(cu + n * step, t);
        buckets[kind].push(a, b);
      }
    }
    const g = new THREE.Group();
    for (const [kind, c, op] of [["minor", "--ink", 0.16], ["major", "--ink", 0.34], ["axis", "--accent", 0.95]]) {
      if (!buckets[kind].length) continue;
      const ls = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(buckets[kind]),
        new THREE.LineBasicMaterial({ color: col(c), transparent: true, opacity: op, depthTest: false }));
      ls.renderOrder = 8; ls.raycast = () => {}; g.add(ls);
    }
    this.localGrid = g; this.scene.add(g);
  }

  // world units per screen pixel at a point (perspective: things further away get more)
  wppAt(p) {
    const h = this.canvas.getBoundingClientRect().height || 1;
    const d = this.camera.position.distanceTo(p);
    return d * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 2 / h;
  }

  // Overlay markers (anchors, snap dots, markers, pattern arrows) keep a constant size on
  // screen: built at unit size, scaled every frame by the world size of a pixel where they are.
  updateScreenSized() {
    const v = new THREE.Vector3();
    const visit = (obj) => {
      const u = obj.userData;
      if (u.px) { obj.getWorldPosition(v); obj.scale.setScalar(this.wppAt(v) * u.px); }
      else if (u.arrowLen) {
        obj.getWorldPosition(v);
        const w = this.wppAt(v), head = u.fixedHead ? w * 14 : Math.min(u.arrowLen * 0.4, w * 14);
        obj.setLength(Math.max(u.arrowLen, head * 1.2), head, w * 8);
      }
    };
    for (const g of [this.overlay, this.temp, this.patternGroup, this.pointGroup]) if (g) g.traverse(visit);
  }

  // An anchor's axes and knob, `px` screen pixels long whatever the zoom.
  triad(fr, px, userData, strong = false) {
    const g = new THREE.Group();
    const o = new THREE.Vector3(0, 0, 0);
    g.position.set(...fr.o); g.userData.px = px;
    const size = 1;
    for (const [axis, c] of [["x", "--x"], ["y", "--y"], ["z", "--z"]]) {
      const d = new THREE.Vector3(...fr[axis]).multiplyScalar(axis === "z" ? size * 1.3 : size);
      const geo = new THREE.BufferGeometry().setFromPoints([o, o.clone().add(d)]);
      const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: col(c), depthTest: false, linewidth: strong ? 2 : 1 }));
      line.renderOrder = 10; g.add(line);
    }
    const knob = new THREE.Mesh(new THREE.SphereGeometry(size * (strong ? 0.22 : 0.16), 12, 8),
      new THREE.MeshBasicMaterial({ color: strong ? col("--sel") : col("--ink"), depthTest: false }));
    knob.position.copy(o); knob.renderOrder = 11; knob.userData = userData;
    g.add(knob);
    return { group: g, knob };
  }

  drawAnchors() {
    this.overlay.clear(); this.knobs = [];
    if (!this.data || !this.show.anchors) return;
    const s = 26;   // px
    for (const b of this.data.bodies) {
      if ((b.role === "removed" && !this.show.ghosts) || this.hidden.has(b.id)) continue;
      for (const a of b.anchors) {
        if (a.name === "origin") continue;
        const t = this.triad(a, s, { kind: "anchor", body: b.id, name: a.name, frame: a, ref: `${b.id}.anchor(${q(a.name)})` });
        this.overlay.add(t.group); this.knobs.push(t.knob);
      }
    }
  }

  // Fit everything, keeping the current direction (or a named one).
  frame(view) {
    const box = this.bbox; if (!box) return;
    const dir = view ? viewDir(view) : this.camera.position.clone().sub(this.controls.target).normalize();
    const { c, dist } = this.fitFor(box);
    this.camera.position.copy(c).addScaledVector(dir, dist);
    this.camera.near = dist / 200; this.camera.far = dist * 40; this.camera.updateProjectionMatrix();
    this.controls.target.copy(c); this.controls.update();
  }

  fitFor(box) {
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3()).length() || 50;
    const vt = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)), ht = vt * this.camera.aspect;
    return { c, dist: s / (2 * Math.min(vt, ht)) * 1.08 };
  }

  // Swing the view to look from a direction: a name (front, top, home...) or [i, j, k] from the ViewCube.
  viewTo(view, fit = false) {
    const to = viewDir(view);
    const target = this.controls.target.clone();
    let dist = this.camera.position.distanceTo(target);
    let toTarget = target;
    if (fit || view === "home") { const f = this.fitFor(this.bbox); dist = f.dist; toTarget = f.c; }
    const from = this.camera.position.clone().sub(target).normalize();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.anim = { t0: performance.now(), dur: reduce ? 0 : 320, from, to, d0: this.camera.position.distanceTo(target), d1: dist, c0: target, c1: toTarget };
  }

  // Swing round to look straight at a plane (sketch mode), centred on its origin.
  lookAt(frame) {
    const to = viewDir(frame.z);
    const target = this.controls.target.clone();
    const d = this.camera.position.distanceTo(target);
    const from = this.camera.position.clone().sub(target).normalize();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.anim = { t0: performance.now(), dur: reduce ? 0 : 320, from, to, d0: d, d1: d, c0: target, c1: new THREE.Vector3(...frame.o) };
  }

  stepAnim(now) {
    const a = this.anim;
    const k = a.dur ? Math.min(1, (now - a.t0) / a.dur) : 1;
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    const dir = slerp(a.from, a.to, e);
    const c = a.c0.clone().lerp(a.c1, e);
    const d = a.d0 + (a.d1 - a.d0) * e;
    this.controls.target.copy(c);
    this.camera.position.copy(c).addScaledVector(dir, d);
    this.camera.near = d / 200; this.camera.far = d * 40; this.camera.updateProjectionMatrix();
    if (k >= 1) this.anim = null;
  }

  // ---------------------------------------------------------------- wheel: pan, zoom, orbit
  bindWheel() {
    this._wheel = { kind: null, t: 0 };
    this.canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      if (this.anim) return;
      if (ev.ctrlKey) return this.zoomAt(ev, Math.exp(ev.deltaY * 0.012));          // pinch (and Ctrl + wheel)
      const kind = this.wheelKind(ev);
      if (kind === "mouse") return this.zoomAt(ev, Math.exp(Math.sign(ev.deltaY) * Math.min(Math.abs(ev.deltaY), 240) * 0.0018));
      if (ev.shiftKey) return this.orbitBy(-ev.deltaX * 0.006, -ev.deltaY * 0.006);
      this.panBy(ev.deltaX, ev.deltaY);
    }, { passive: false });
  }

  // A trackpad sends small, fractional, two-axis deltas; a mouse wheel sends notches. A gesture keeps
  // its first verdict for a moment, so a scroll doesn't flip between pan and zoom halfway.
  wheelKind(ev) {
    const now = performance.now(), w = this._wheel;
    if (w.kind && now - w.t < 350) { w.t = now; return w.kind; }
    let kind;
    if (ev.deltaMode !== 0) kind = "mouse";
    else if (ev.deltaX !== 0) kind = "trackpad";
    else if (ev.wheelDeltaY && Math.abs(ev.wheelDeltaY) === Math.abs(ev.deltaY) * 3) kind = "trackpad";
    else if (ev.wheelDeltaY && ev.wheelDeltaY % 120 === 0) kind = "mouse";
    else kind = !Number.isInteger(ev.deltaY) || Math.abs(ev.deltaY) < 40 ? "trackpad" : "mouse";
    w.kind = kind; w.t = now;
    return kind;
  }

  // Slide the view so the model follows the fingers, one screen pixel per pixel of scroll.
  panBy(dx, dy) {
    const wpp = this.worldPerPx();
    const m = this.camera.matrixWorld;
    const right = new THREE.Vector3().setFromMatrixColumn(m, 0), up = new THREE.Vector3().setFromMatrixColumn(m, 1);
    const off = right.multiplyScalar(dx * wpp).add(up.multiplyScalar(-dy * wpp));
    this.camera.position.add(off); this.controls.target.add(off);
  }

  // Zoom toward (factor < 1) or away from the point under the cursor.
  zoomAt(ev, factor) {
    factor = Math.min(Math.max(factor, 0.5), 2);
    const cam = this.camera.position, t = this.controls.target;
    const hits = this.hits(ev, { edges: false });
    let p = hits.length ? hits[0].point.clone() : null;
    if (!p) {
      this.ray.setFromCamera(this.ndc(ev), this.camera);
      const n = cam.clone().sub(t).normalize();
      p = new THREE.Vector3();
      if (!this.ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, t), p)) p = t.clone();
    }
    const dist = cam.distanceTo(t) * factor;
    if (dist < 0.05 && factor < 1) return;
    if (dist > 50000 && factor > 1) return;
    cam.sub(p).multiplyScalar(factor).add(p);
    t.sub(p).multiplyScalar(factor).add(p);
    const d = cam.distanceTo(t);
    this.camera.near = d / 200; this.camera.far = d * 40; this.camera.updateProjectionMatrix();
  }

  // ---------------------------------------------------------------- box selection
  // What a selection box takes: kind 'body' | 'face' | 'edge'. mode 'window' (drawn left to
  // right: only what is entirely inside) or 'crossing' (right to left: anything it touches).
  boxPick(rect, mode, kind) {
    const r = this.canvas.getBoundingClientRect();
    const box = { x0: rect.x0 - r.left, y0: rect.y0 - r.top, x1: rect.x1 - r.left, y1: rect.y1 - r.top };
    const v = new THREE.Vector3();
    const toS = (x, y, z) => {
      v.set(x, y, z).project(this.camera);
      return v.z > 1 || v.z < -1 ? null : [(v.x + 1) / 2 * r.width, (1 - v.y) / 2 * r.height];
    };
    const inside = (p) => p && p[0] >= box.x0 && p[0] <= box.x1 && p[1] >= box.y0 && p[1] <= box.y1;
    const segHits = (a, b) => a && b && segRect(a, b, box);
    const triHits = (a, b, c) => a && b && c && (inside(a) || inside(b) || inside(c) || segHits(a, b) || segHits(b, c) || segHits(c, a) ||
      pointInTri([box.x0, box.y0], a, b, c));
    const out = [];
    if (kind === "edge") {
      for (const l of this.edgeLines) {
        const pts = l.userData.points;
        const ps = [];
        for (let i = 0; i < pts.length; i += 3) ps.push(toS(pts[i], pts[i + 1], pts[i + 2]));
        const all = ps.every(inside);
        const any = mode === "crossing" && (ps.some(inside) || ps.some((p, i) => i && segHits(ps[i - 1], p)));
        if (all || any) out.push({ kind: "edge", body: l.userData.body, ref: l.userData.ref, label: l.userData.label, key: "edge:" + l.userData.ref });
      }
      return out;
    }
    const perBody = {};
    for (const m of this.faceMeshes) {
      if (m.userData.role === "removed") continue;
      const pos = m.geometry.getAttribute("position"), idx = m.geometry.getIndex();
      const ps = [];
      for (let i = 0; i < pos.count; i++) ps.push(toS(pos.getX(i), pos.getY(i), pos.getZ(i)));
      let all = ps.every(inside), any = false;
      if (!all && mode === "crossing") {
        any = ps.some(inside);
        for (let i = 0; !any && idx && i < idx.count; i += 3) any = triHits(ps[idx.getX(i)], ps[idx.getX(i + 1)], ps[idx.getX(i + 2)]);
      }
      const u = m.userData;
      if (kind === "face") {
        if (all || any) out.push({ kind: "face", body: u.body, ref: u.ref, label: u.label, entry: u.face, key: "face:" + u.ref });
      } else {
        const b = perBody[u.body] || (perBody[u.body] = { all: true, any: false });
        b.all = b.all && all; b.any = b.any || any || all;
      }
    }
    if (kind !== "face") for (const [id, b] of Object.entries(perBody)) if (b.all || (mode === "crossing" && b.any)) out.push({ kind: "body", body: id, ref: id, key: "body:" + id });
    return out;
  }

  // Orbit by angles (radians): around world Z, and up/down.
  orbitBy(dAz, dEl) {
    const t = this.controls.target, off = this.camera.position.clone().sub(t);
    const sph = new THREE.Spherical().setFromVector3(new THREE.Vector3(off.x, off.z, -off.y));
    sph.theta += dAz; sph.phi = Math.min(Math.PI - 1e-3, Math.max(1e-3, sph.phi + dEl));
    const v = new THREE.Vector3().setFromSpherical(sph);
    this.camera.position.copy(t).add(new THREE.Vector3(v.x, -v.z, v.y));
    this.camera.lookAt(t);
  }

  // ---------------------------------------------------------------- body lookup
  body(id) { return this.data && this.data.bodies.find((b) => b.id === id); }

  designAnchorFor(bodyId, faceLabel) {
    const b = this.body(bodyId);
    return b && b.anchors.find((a) => a.name === faceLabel) ? `${bodyId}.anchor(${q(faceLabel)})` : null;
  }

  // ---------------------------------------------------------------- highlighting
  applyHighlights() {
    const h = this.highlight;
    const acc = col("--accent"), sel = col("--sel"), black = new THREE.Color(0);
    for (const m of this.faceMeshes) {
      const u = m.userData;
      let e = black, k = 0;
      if (h.focus.has(u.body)) { e = acc; k = 0.3; }
      if (h.selected.has("body:" + u.body) || h.selected.has("face:" + u.ref)) { e = sel; k = 0.45; }
      if (h.hover && (h.hover === "body:" + u.body || h.hover === "face:" + u.ref)) { e = acc; k = 0.42; }
      m.material.emissive.copy(e).multiplyScalar(k);
    }
    for (const o of this._edgeHi || []) { o.parent && o.parent.remove(o); o.geometry.dispose(); o.material.dispose(); }
    this._edgeHi = []; this._edgeHiMats = [];
    const r = this.canvas.getBoundingClientRect();
    for (const l of this.edgeLines) {
      const u = l.userData;
      const on = h.selected.has("edge:" + u.ref), hov = h.hover === "edge:" + u.ref;
      l.material.color.copy(hov ? acc : on ? sel : u.body && this.body(u.body)?.role === "reference" ? col("--ref") : col("--edge"));
      l.material.opacity = hov || on ? 1 : 0.85;
      if (hov || on) {
        const geo = new LineSegmentsGeometry().setPositions(l.geometry.getAttribute("position").array);
        const mat = new LineMaterial({ color: (hov ? acc : sel).getHex(), linewidth: hov ? EDGE_HOVER_PX : EDGE_SELECTED_PX,
          transparent: true, opacity: 0.95, depthTest: true, depthWrite: false });
        mat.resolution.set(r.width || 1, r.height || 1);
        const fat = new LineSegments2(geo, mat);
        fat.renderOrder = 5; fat.raycast = () => {};       // drawing only; picking uses the thin line
        fat.userData = { kind: "edge-highlight", ref: u.ref };
        if (this.clip) mat.clippingPlanes = [this.clip];
        l.add(fat);
        this._edgeHi.push(fat); this._edgeHiMats.push(mat);
      }
    }
  }

  // see-through bodies: a = 1 opaque ... 0.05 almost invisible; edges stay readable
  setBodyOpacity(id, a) {
    a = Math.max(0.05, Math.min(1, a));
    if (a >= 1) this.opacity.delete(id); else this.opacity.set(id, a);
    for (const m of this.faceMeshes) {
      if (m.userData.body !== id) continue;
      const op = (m.material.userData.baseOpacity ?? 1) * a;
      m.material.opacity = op; m.material.transparent = op < 1; m.material.depthWrite = op >= 1;
      m.renderOrder = op < 1 ? 2 : 0;
      m.material.needsUpdate = true;
    }
  }

  // A point picked from a list: a dot with its axes, drawn over everything. strong: the marked
  // one (selection colour); otherwise the hovered one (accent).
  setPointMark(fr, strong = false) {
    this.pointGroup.clear();
    if (!fr || !fr.o) return;
    const g = fr.x && fr.z ? this.triad({ o: fr.o, x: fr.x, z: fr.z, y: new THREE.Vector3(...fr.z).cross(new THREE.Vector3(...fr.x)).toArray() }, 30, { kind: "point-mark" }, true).group : new THREE.Group();
    if (!fr.x) { g.position.set(...fr.o); g.userData.px = 30; }
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.2, 14, 10), new THREE.MeshBasicMaterial({ color: strong ? col("--sel") : col("--accent"), depthTest: false }));
    dot.renderOrder = 12; dot.raycast = () => {};
    g.add(dot);
    g.traverse((o) => { o.raycast = () => {}; });
    this.pointGroup.add(g);
  }

  setHover(key) { if (this.highlight.hover !== key) { this.highlight.hover = key; this.applyHighlights(); } }
  setSelected(keys) { this.highlight.selected = new Set(keys); this.applyHighlights(); }

  // ---------------------------------------------------------------- picking
  ndc(ev) {
    const r = this.canvas.getBoundingClientRect();
    return new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  }

  worldPerPx() {
    const r = this.canvas.getBoundingClientRect();
    return this.camera.position.distanceTo(this.controls.target) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 2 / r.height;
  }

  toScreen(p) {
    const r = this.canvas.getBoundingClientRect();
    const v = new THREE.Vector3(...p).project(this.camera);
    return [(v.x + 1) / 2 * r.width, (1 - v.y) / 2 * r.height];
  }

  // Raw hits under the cursor, nearest first: {kind, object, point}
  hits(ev, { edges = true, ghosts = false } = {}) {
    this.ray.setFromCamera(this.ndc(ev), this.camera);
    this.ray.params.Line.threshold = this.worldPerPx() * (this._edgePx || 6);
    const objs = this.faceMeshes.filter((m) => ghosts || m.userData.role !== "removed");
    if (edges) objs.push(...this.edgeLines);
    const hs = this.ray.intersectObjects(objs, false);
    return this.clip ? hs.filter((h) => this.clip.distanceToPoint(h.point) >= -1e-6) : hs;
  }

  pickKnob(ev) {
    if (!this.knobs.length) return null;
    this.ray.setFromCamera(this.ndc(ev), this.camera);
    const h = this.ray.intersectObjects(this.knobs, false);
    return h.length ? h[0].object.userData : null;
  }

  // Fusion-style: the edge under the cursor (within a few pixels) wins, otherwise the face.
  pickAuto(ev) {
    const e = this.pick(ev, "edge", 5);
    return e || this.pick(ev, "face");
  }

  // Pick with a selection filter: "body" | "face" | "edge"
  pick(ev, filter, edgePx = 6) {
    this._edgePx = edgePx;
    const hs = this.hits(ev, { edges: filter === "edge" });
    if (!hs.length) return null;
    const wpp = this.worldPerPx();
    if (filter === "edge") {
      const first = hs[0];
      const e = hs.find((h) => h.object.userData.kind === "edge" && h.distance < first.distance + wpp * (edgePx + 8));
      return e ? { kind: "edge", ...e.object.userData, point: e.point.toArray() } : null;
    }
    let f = hs.find((h) => h.object.userData.kind === "face");
    if (!f) return null;
    // a profile drawn on a face lies exactly on it: at the same depth, the profile wins
    const prof = hs.find((h) => h.object.userData.kind === "face" && this.body(h.object.userData.body)?.kind === "profile" &&
      h.distance - f.distance < wpp * 0.5);
    if (prof) f = prof;
    const u = f.object.userData;
    const normal = f.face ? orientNormal(f, this.camera) : null;
    if (filter === "face") return { kind: "face", ...u, point: f.point.toArray(), normal };
    return { kind: "body", body: u.body, ref: u.body, label: u.label, point: f.point.toArray(), normal, face: u.face };
  }

  // ---------------------------------------------------------------- snap candidates on a face
  // Everything you can snap to on the face under the cursor: the face center (design
  // anchor when one exists), hole and boss centers, corners and midpoints.
  // Everything you can snap to on the face under the cursor, as computed by the engine:
  // the face center (its design anchor when one exists), hole and boss centers, corners
  // and midpoints. A hole's wall offers the hole's center first.
  snapCandidates(hit) {
    const f = hit.face;
    const snaps = (f && f.snaps && f.snaps.length) ? f.snaps
      : [{ ref: `${hit.body}.face(${q(hit.label)}).anchor()`, kind: "center", ...frameFromNormal(hit.point, hit.normal || [0, 0, 1]) }];
    return snaps.map((s) => {
      const z = new THREE.Vector3(...s.z), x = new THREE.Vector3(...s.x);
      return { ref: s.ref, kind: s.kind, p: s.o, body: hit.body, frame: { o: s.o, x: s.x, z: s.z, y: z.clone().cross(x).toArray() } };
    });
  }

  // Where a face's grid starts (and free points are measured from): its centre when that is a
  // design anchor (a box's side: symmetric, so its corners sit on the grid too), otherwise the
  // corner of its outline at the low X, low Y end, so the grid lines up with the face's edges
  // instead of with the middle of an odd shape.
  gridBase(cands) {
    const center = cands.find((c) => c.kind === "center") || cands[0];
    if (!center || !center.frame) return center || null;
    if (!/\.face\(/.test(center.ref)) return center;
    const corners = cands.filter((c) => c.kind === "corner");
    if (!corners.length) return center;
    const fr = center.frame, o = new THREE.Vector3(...fr.o), x = new THREE.Vector3(...fr.x), y = new THREE.Vector3(...fr.y);
    const loc = (p) => { const d = new THREE.Vector3(...p).sub(o); return [d.dot(x), d.dot(y)]; };
    const key = (c) => { const [u, v] = loc(c.p); return [Math.round((u + v) * 1e4), Math.round(u * 1e4)]; };
    const best = corners.reduce((a, c) => { const ka = key(a), kc = key(c); return kc[0] < ka[0] || (kc[0] === ka[0] && kc[1] < ka[1]) ? c : a; });
    return { ...best, frame: { o: best.p, x: fr.x, y: fr.y, z: fr.z } };
  }

  // The face feature (corner, centre, hole, edge middle...) nearest a point on the face, as a
  // frame with the face's own axes: free points are measured from it and the grid starts there,
  // so grid points near a corner fall exactly on that corner's lines.
  nearestFeature(cands, p, axes) {
    let best = null, bd = Infinity;
    for (const c of cands) {
      if (!c || c.kind === "free" || c.kind === "bed" || !c.p) continue;
      const d = Math.hypot(c.p[0] - p[0], c.p[1] - p[1], c.p[2] - p[2]);
      if (d < bd - 1e-9) { bd = d; best = c; }
    }
    if (!best) return null;
    const fr = axes || best.frame;
    return { ...best, frame: { o: best.p, x: fr.x, y: fr.y, z: fr.z } };
  }

  showSnaps(cands, active) {
    this.overlay.children.filter((o) => o.userData.snap).forEach((o) => this.overlay.remove(o));
    if (!cands) { this.drawAnchors(); return; }
    cands.forEach((c, i) => {
      const on = i === active;
      const geo = new THREE.SphereGeometry(1, 12, 8);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: on ? col("--sel") : col("--accent"), depthTest: false }));
      m.position.set(...c.p); m.renderOrder = 12; m.userData.snap = true;
      m.userData.px = on ? 6.5 : c.kind === "center" ? 5 : 3.6;     // radius in screen pixels
      this.overlay.add(m);
    });
  }

  // Index of the candidate nearest the cursor on screen within 16 px, or -1.
  nearestSnap(ev, cands) {
    const r = this.canvas.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    let best = -1, bestD = 16;
    cands.forEach((c, i) => {
      const [sx, sy] = this.toScreen(c.p);
      const d = Math.hypot(sx - mx, sy - my);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  // ---------------------------------------------------------------- live transforms (move gizmo)
  setBodyTransform(id, m4) {
    const g = this.groups && this.groups[id];
    if (!g) return;
    g.matrixAutoUpdate = false;
    g.matrix.copy(m4);
    g.matrixWorldNeedsUpdate = true;
  }

  // A body's origin frame in the given scene data (defaults to what is shown).
  bodyFrame(id, data = this.data) {
    const b = data && data.bodies.find((x) => x.id === id);
    const a = b && b.anchors.find((x) => x.name === "origin");
    return a ? { o: a.o, x: a.x, y: a.y, z: a.z } : null;
  }

  // ---------------------------------------------------------------- plane picking (click-to-size)
  planePoint(ev, frame) {
    this.ray.setFromCamera(this.ndc(ev), this.camera);
    const n = new THREE.Vector3(...frame.z), o = new THREE.Vector3(...frame.o);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(n, o);
    const p = new THREE.Vector3();
    if (!this.ray.ray.intersectPlane(plane, p)) return null;
    const d = p.clone().sub(o);
    return { world: p.toArray(), x: d.dot(new THREE.Vector3(...frame.x)), y: d.dot(new THREE.Vector3(...frame.y)) };
  }

  // ---------------------------------------------------------------- temporary graphics
  clearTemp() { this.temp.clear(); }

  // ---------------------------------------------------------------- pattern preview
  // See-through copies of the seed body (copy = matrix @ seed), plus arrows for the
  // directions (rect) or the axis and turning sense (circular). Not pickable.
  clearPattern() {
    if (!this.patternGroup) return;
    this.scene.remove(this.patternGroup);
    this.patternGroup.traverse((o) => { if (o.material && o.userData.own) o.material.dispose(); if (o.userData.ownGeo) o.geometry.dispose(); });
    this.patternGroup = null;
  }

  showPattern(info, copies) {
    this.clearPattern();
    if (!info || !info.kind) return;
    const g = new THREE.Group(); g.renderOrder = 12;
    const src = this.groups[info.seed];
    const fill = new THREE.MeshStandardMaterial({ color: col("--accent"), transparent: true, opacity: 0.38, depthWrite: false, side: THREE.DoubleSide, roughness: 0.7 });
    const line = new THREE.LineBasicMaterial({ color: col("--accent"), transparent: true, opacity: 0.9 });
    fill.userData = {}; g.userData.mats = [fill, line];
    if (src) {
      for (const m16 of copies) {
        const c = new THREE.Group(); c.matrixAutoUpdate = false;
        c.matrix.set(...m16);
        src.traverse((o) => {
          if (o.isMesh && o.userData.kind === "face") c.add(new THREE.Mesh(o.geometry, fill));
          else if ((o.isLine || o.isLineSegments) && o.userData.kind === "edge") c.add(new o.constructor(o.geometry, line));
        });
        g.add(c);
      }
    }
    const L = this.modelSize;
    const arrow = (o, d, len, colorVar, label) => {
      const dir = new THREE.Vector3(...d).normalize();
      const head = Math.min(len * 0.35, L * 0.05);
      const a = new THREE.ArrowHelper(dir, new THREE.Vector3(...o), len, col(colorVar), head, head * 0.55);
      a.traverse((x) => { if (x.material) { x.material.depthTest = false; x.material.transparent = true; } x.renderOrder = 14; });
      a.userData.arrowLen = len;          // the length is real (the pattern's extent); the head stays screen-sized
      g.add(a);
      if (label) {
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: textSprite(label, css(colorVar)), depthTest: false }));
        sp.position.copy(new THREE.Vector3(...o).addScaledVector(dir, len)); sp.center.set(-0.15, -0.15); sp.userData.px = 20; sp.renderOrder = 15;
        g.add(sp);
      }
    };
    if (info.kind === "rect") {
      const colors = ["--accent", "--sel", "--ok"];
      info.dirs.forEach((dd, i) => {
        if (i > 0 && dd.n < 2) return;
        const sign = dd.s < 0 ? -1 : 1;
        const len = dd.n > 1 && dd.s ? Math.abs(dd.s) * (dd.n - 1) : L * 0.12;
        arrow(info.origin, dd.d.map((v) => v * sign), Math.max(len, L * 0.06), colors[i], String(i + 1));
      });
    } else if (info.kind === "circ") {
      const ax = new THREE.Vector3(...info.axis).normalize(), o = new THREE.Vector3(...info.origin);
      const half = L * 0.3;
      arrow(o.clone().addScaledVector(ax, -half).toArray(), info.axis, half * 2, "--accent", null);
      // the turning sense: an arc from the seed around the axis, ending in a cone
      let r = new THREE.Vector3(...info.seed_origin).sub(o);
      r.addScaledVector(ax, -r.dot(ax));
      if (r.length() < L * 0.02) { r = new THREE.Vector3(1, 0, 0).cross(ax); if (r.length() < 0.1) r = new THREE.Vector3(0, 1, 0).cross(ax); r.setLength(L * 0.1); }
      const base = new THREE.Vector3(...info.seed_origin).sub(r);
      const sweep = THREE.MathUtils.degToRad(Math.sign(info.step || 1) * Math.min(Math.abs(info.step || 30), 50));
      const pts = [];
      for (let k = 0; k <= 24; k++) pts.push(r.clone().applyAxisAngle(ax, (sweep * k) / 24).add(base));
      const arc = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col("--sel"), depthTest: false }));
      arc.renderOrder = 14; arc.userData.ownGeo = true; g.add(arc);
      const tip = pts[pts.length - 1], prev = pts[pts.length - 2];
      arrow(prev.toArray(), tip.clone().sub(prev).toArray(), Math.max(tip.distanceTo(prev), L * 0.03), "--sel", null);
      g.children[g.children.length - 1].userData.fixedHead = true;
    }
    this.patternGroup = g;
    this.scene.add(g);
  }

  line(a, b, colorVar = "--sel") {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]);
    const l = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: col(colorVar), dashSize: this.modelSize * 0.02, gapSize: this.modelSize * 0.012, depthTest: false }));
    l.computeLineDistances(); l.renderOrder = 13; this.temp.add(l);
  }

  marker(p, colorVar = "--sel", scale = 1.6) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 8), new THREE.MeshBasicMaterial({ color: col(colorVar), depthTest: false }));
    m.position.set(...p); m.renderOrder = 13; m.userData.px = 3.6 * scale; this.temp.add(m);
  }

  frameMarker(fr) {
    const t = this.triad(fr, 40, {}, true);
    this.temp.add(t.group);
  }

  circle(frame, r) {
    const pts = [];
    const o = new THREE.Vector3(...frame.o), x = new THREE.Vector3(...frame.x), y = new THREE.Vector3(...frame.y);
    for (let i = 0; i <= 64; i++) { const a = i / 64 * Math.PI * 2; pts.push(o.clone().addScaledVector(x, r * Math.cos(a)).addScaledVector(y, r * Math.sin(a))); }
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col("--sel"), depthTest: false }));
    l.renderOrder = 13; this.temp.add(l);
  }
}

// 2D helpers for box selection
function segRect(a, b, r) {
  const cs = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
  for (let i = 0; i < 4; i++) if (segSeg(a, b, cs[i], cs[(i + 1) % 4])) return true;
  return false;
}
function segSeg(p, q, r, s) {
  const o = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  return o(p, q, r) !== o(p, q, s) && o(r, s, p) !== o(r, s, q);
}
function pointInTri(p, a, b, c) {
  const s = (u, v, w) => (u[0] - w[0]) * (v[1] - w[1]) - (v[0] - w[0]) * (u[1] - w[1]);
  const d1 = s(p, a, b), d2 = s(p, b, c), d3 = s(p, c, a);
  return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
}

// A frame on a face from its normal: X = +X on top/bottom, else X so that Y points up.
export function frameFromNormal(o, n) {
  const z = new THREE.Vector3(...n).normalize();
  let x = Math.abs(z.z) > 0.999 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1).cross(z).normalize();
  const y = z.clone().cross(x);
  return { o: [...o], x: x.toArray(), y: y.toArray(), z: z.toArray() };
}

// Triangle normal from the mesh's stored vertex normals (outward), not the winding.
function orientNormal(hit, camera) {
  const geo = hit.object.geometry, nAttr = geo.getAttribute("normal");
  const n = new THREE.Vector3();
  for (const i of [hit.face.a, hit.face.b, hit.face.c]) n.add(new THREE.Vector3().fromBufferAttribute(nAttr, i));
  return n.normalize().toArray();
}

const VIEWS = {
  front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 0, 1], bottom: [0, 0, -1],
  home: [1, -1.25, 0.9], iso: [1, -1.25, 0.9],
};

// Direction from the target to the camera. Straight up or down is nudged so "front" stays at the bottom.
// world.anchor("XY" / "XZ" / "YZ"): the frames the engine gives them (refs.py WORLD_PLANES)
export const ORIGIN_PLANES = {
  XY: { x: [1, 0, 0], z: [0, 0, 1], color: "--z" },
  XZ: { x: [1, 0, 0], z: [0, -1, 0], color: "--y" },
  YZ: { x: [0, 1, 0], z: [1, 0, 0], color: "--x" },
};

export function viewDir(view) {
  const d = new THREE.Vector3(...(Array.isArray(view) ? view : VIEWS[view] || VIEWS.home));
  if (Math.abs(d.x) < 1e-9 && Math.abs(d.y) < 1e-9) d.y = -1e-3 * Math.sign(d.z || 1);
  return d.normalize();
}

function slerp(a, b, t) {
  const dot = Math.min(1, Math.max(-1, a.dot(b)));
  const th = Math.acos(dot) * t;
  if (Math.abs(dot) > 0.9995) return a.clone().lerp(b, t).normalize();
  const rel = b.clone().sub(a.clone().multiplyScalar(dot)).normalize();
  return a.clone().multiplyScalar(Math.cos(th)).add(rel.multiplyScalar(Math.sin(th))).normalize();
}

function textSprite(text, color) {
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const g = c.getContext("2d");
  g.fillStyle = color || "#888"; g.font = '600 44px "IBM Plex Sans Condensed", "Arial Narrow", sans-serif';
  g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 32, 34);
  return new THREE.CanvasTexture(c);
}
