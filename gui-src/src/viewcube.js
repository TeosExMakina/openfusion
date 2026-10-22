// ViewCube: a small cube that turns with the camera. Its 6 faces, 12 edges and 8 corners
// are all clickable and swing the view to look from that direction. Drag it to orbit.
import * as THREE from "three";
import { css } from "./api.js";

const col = (n) => new THREE.Color(css(n) || "#888");
const LABELS = [
  // outward normal, up direction on the label, text
  [[0, 0, 1], [0, 1, 0], "TOP"], [[0, 0, -1], [0, 1, 0], "BOTTOM"],
  [[0, -1, 0], [0, 0, 1], "FRONT"], [[0, 1, 0], [0, 0, 1], "BACK"],
  [[1, 0, 0], [0, 0, 1], "RIGHT"], [[-1, 0, 0], [0, 0, 1], "LEFT"],
];

export class ViewCube {
  constructor(canvas, vp, homeBtn) {
    this.canvas = canvas; this.vp = vp;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 20);
    this.pieces = [];
    this.build();
    this.hot = null;
    this.ray = new THREE.Raycaster();
    canvas.addEventListener("pointermove", (e) => this.move(e));
    canvas.addEventListener("pointerleave", () => this.setHot(null));
    canvas.addEventListener("pointerdown", (e) => this.down(e));
    window.addEventListener("pointerup", (e) => this.up(e));
    window.addEventListener("pointermove", (e) => this.dragMove(e));
    if (homeBtn) homeBtn.addEventListener("click", () => vp.viewTo("home"));
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
    vp.onFrame.push(() => this.render());
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (r.width) this.renderer.setSize(r.width, r.height, false);
  }

  build() {
    // 26 pieces: faces are 0.6 wide in the middle of each side, edges and corners 0.2 wide.
    const pos = { "-1": -0.4, 0: 0, 1: 0.4 }, size = { "-1": 0.2, 0: 0.6, 1: 0.2 };
    for (const i of [-1, 0, 1]) for (const j of [-1, 0, 1]) for (const k of [-1, 0, 1]) {
      if (!i && !j && !k) continue;
      const g = new THREE.BoxGeometry(size[i], size[j], size[k]);
      const kind = Math.abs(i) + Math.abs(j) + Math.abs(k);   // 1 face, 2 edge, 3 corner
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: col(kind === 1 ? "--cube-face" : "--cube-edge") }));
      m.position.set(pos[i], pos[j], pos[k]);
      m.userData = { dir: [i, j, k], kind };
      this.scene.add(m); this.pieces.push(m);
    }
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), new THREE.LineBasicMaterial({ color: col("--cube-line") }));
    this.scene.add(outline);
    for (const [n, up, text] of LABELS) {
      const tex = this.labelTexture(text);
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(0.62, 0.62), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
      const N = new THREE.Vector3(...n), U = new THREE.Vector3(...up), R = U.clone().cross(N);
      plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(R, U, N));
      plane.position.copy(N.multiplyScalar(0.505));
      plane.raycast = () => {};
      this.scene.add(plane);
    }
    // world axes along three edges from the bottom-left-front corner
    const o = new THREE.Vector3(-0.5, -0.5, -0.5);
    for (const [d, c] of [[[1, 0, 0], "--x"], [[0, 1, 0], "--y"], [[0, 0, 1], "--z"]]) {
      const geo = new THREE.CylinderGeometry(0.018, 0.018, 1.25, 6);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: col(c) }));
      const dir = new THREE.Vector3(...d);
      m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      m.position.copy(o.clone().addScaledVector(dir, 0.62));
      m.raycast = () => {};
      this.scene.add(m);
    }
  }

  labelTexture(text) {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d");
    g.fillStyle = css("--cube-ink") || "#333";
    g.font = `600 ${text.length > 5 ? 50 : 58}px "IBM Plex Sans Condensed", "Arial Narrow", sans-serif`;
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(text, 128, 132);
    const t = new THREE.CanvasTexture(c); t.anisotropy = 4;
    return t;
  }

  render() {
    const cam = this.vp.camera, target = this.vp.controls.target;
    const dir = cam.position.clone().sub(target).normalize();
    this.camera.position.copy(dir.multiplyScalar(5));
    this.camera.quaternion.copy(cam.quaternion);
    const s = 0.98;
    this.camera.left = -s; this.camera.right = s; this.camera.top = s; this.camera.bottom = -s;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }

  pick(e) {
    const r = this.canvas.getBoundingClientRect();
    const p = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(p, this.camera);
    const h = this.ray.intersectObjects(this.pieces, false);
    return h.length ? h[0].object : null;
  }

  setHot(m) {
    if (m === this.hot) return;
    if (this.hot) this.hot.material.color.copy(col(this.hot.userData.kind === 1 ? "--cube-face" : "--cube-edge"));
    this.hot = m;
    if (m) m.material.color.copy(col("--accent"));
    this.canvas.style.cursor = m ? "pointer" : "";
    this.canvas.title = m ? describe(m.userData.dir) : "";
  }

  move(e) { if (!this.drag) this.setHot(this.pick(e)); }

  down(e) {
    if (e.button !== 0) return;
    this.drag = { x: e.clientX, y: e.clientY, moved: false, piece: this.pick(e) };
    e.preventDefault();
  }

  dragMove(e) {
    if (!this.drag) return;
    const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
    if (!this.drag.moved && Math.hypot(dx, dy) < 4) return;
    this.drag.moved = true;
    this.drag.x = e.clientX; this.drag.y = e.clientY;
    this.vp.orbitBy(-dx * 0.012, -dy * 0.012);
  }

  up(e) {
    if (!this.drag) return;
    const d = this.drag; this.drag = null;
    if (!d.moved && d.piece) this.vp.viewTo(d.piece.userData.dir);
  }
}

function describe([i, j, k]) {
  const n = [k > 0 ? "top" : k < 0 ? "bottom" : "", j < 0 ? "front" : j > 0 ? "back" : "", i > 0 ? "right" : i < 0 ? "left" : ""].filter(Boolean);
  return "View from " + n.join(" ");
}
