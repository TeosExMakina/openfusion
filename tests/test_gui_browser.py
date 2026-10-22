"""Every toolbar tool, driven through its dialog in a real browser, writes Python that builds.

Needs Playwright and a Chromium (skipped otherwise):
    pip install playwright && python -m playwright install chromium
Set OPENFUSION_CHROME to use a particular Chromium binary.
"""
import os
import re
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

from openfusion import Engine  # noqa: E402
from openfusion.gui.server import make_server  # noqa: E402

START = '''from openfusion import *

plate = box(length=40, width=30, height=10)
'''

# (tool, field values, the operation the new last line must call)
# new bodies are named body1, body2... in the order they are made: body1/body2 boxes, body3 cylinder,
# body4 cone, body5 sphere, body6 torus, body7 coil, body8 polygon, body9 loft, body10 revolve, body11 sweep
STEPS = [
    ("box", {"at": "world.point(60, 0, 0)", "length": "10", "width": "10", "height": "10"}, "box"),
    ("box", {"at": "world.point(60, 60, 0)", "length": "10", "width": "10", "height": "10"}, "box"),
    ("cylinder", {"at": 'plate.anchor("+Z")', "r": "3", "h": "5"}, "cylinder"),
    ("thread", {"face": 'body3.face("side")', "length": "3", "allowance": "0.2"}, "thread"),
    ("hole", {"at": 'plate.anchor("+Z").offset(x=12)', "size": "M3", "fit": "tapped", "head": "counterbore"}, "hole"),
    ("text", {"text": "OF", "at": 'plate.anchor("+Z").offset(x=-10)', "size": "4", "bold": "True"}, "text"),
    ("extrude", {"face": "text1", "distance": "-0.4"}, "extrude"),
    ("plane", {"at": 'plate.anchor("+Z")', "offset": "5", "angle": "10"}, "plane"),
    ("plane_edge", {"about": 'plate.edge("+Y+Z")', "angle": "30"}, "plane"),
    ("midplane", {"mid": '[plate.face("-X"), plate.face("+X")]'}, "plane"),
    ("axis", {"ref": 'plate.edge("+X+Y")'}, "axis"),
    ("cone", {"at": "world.point(0, 60, 0)", "r1": "4", "r2": "1", "h": "6"}, "cone"),
    ("sphere", {"at": "world.point(-30, 0, 0)", "r": "3"}, "sphere"),
    ("torus", {"at": "world.point(-30, 40, 0)", "R": "6", "r": "1"}, "torus"),
    ("coil", {"at": "world.point(90, 40, 0)", "R": "6", "turns": "2", "pitch": "4", "size": "1"}, "coil"),
    ("polygon", {"at": "world.point(110, 0, 0)", "n": "6", "r": "4", "h": "3"}, "polygon"),
    ("profile_rect", {"at": "world.point(0, -60, 0)", "length": "10", "width": "6", "origin": "center"}, "profile"),
    ("profile_circle", {"at": "world.point(0, -60, 20)", "r": "3"}, "profile"),
    ("loft", {"sections": "[profile1, profile2]"}, "loft"),
    ("profile_line", {"at": "world.point(0, -100, 0)", "points": "(10, 0), (14, 0), (14, 6), (10, 6)", "closed": "True"}, "profile"),
    ("revolve", {"profile": "profile3", "axis": '"Y"', "angle": "360"}, "revolve"),
    ("profile_line", {"at": "world.point(-80, -40, 0)", "points": "(0, 0), (30, 0), (30, 20)", "closed": "False"}, "profile"),
    ("profile_circle", {"at": 'profile4.anchor("start")', "r": "2"}, "profile"),
    ("sweep", {"profile": "profile5", "path": "profile4"}, "sweep"),
    ("extrude", {"face": 'plate.face("-Y")', "distance": "2"}, "extrude"),
    ("fillet", {"edges": '[plate.edge("+X-Z"), plate.edge("-X-Z")]', "r": "1"}, "fillet"),
    ("chamfer", {"edges": '[body1.edge("+X+Z")]', "d": "1"}, "chamfer"),
    ("combine", {"target": "plate", "tools": "[body3]"}, "join"),
    ("shell", {"body": "body1", "thickness": "1", "faces": '[body1.face("+Z")]'}, "shell"),
    ("draft", {"plane": 'body2.face("-Z")', "faces": '[body2.face("+X")]', "angle": "3"}, "draft"),
    ("move", {"body": "body5", "dx": "5"}, "move"),
    ("move_p2p", {"point": 'body4.anchor("origin")', "to": "world.point(0, 70, 0)", "body": "body4"}, "move"),
    ("place", {"source": 'body6.anchor("origin")', "onto": "world.point(-40, 40, 0)"}, "place"),
    ("rotate", {"body": "body8", "angle": "30"}, "rotate"),
    ("place_on_bed", {"body": "body1"}, "place_on_bed"),
    ("split", {"body": "body2", "plane": 'body2.anchor("center")'}, "split"),
    ("scale", {"body": "body5", "factor": "2"}, "scale"),
    ("pattern_rect", {"body": "body8", "x.0": "3", "x.1": "10"}, "pattern_rect"),
    ("pattern_circ", {"body": "body5", "count": "4"}, "pattern_circ"),
    ("mirror", {"body": "body6", "plane": '"YZ"'}, "mirror"),
    ("copy", {"body": "body1"}, "copy"),
    ("set_anchor", {"body": "plate", "name": "mount", "at": 'plate.anchor("+Z").offset(x=5)'}, "set_anchor"),
]


def chrome():
    for c in (os.environ.get("OPENFUSION_CHROME"), "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if c and os.path.exists(c):
            return c
    return None


@pytest.fixture
def page(tmp_path):
    f = tmp_path / "gui.ofs"
    f.write_text(START)
    srv, ws = make_server(str(f), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            try:
                b = p.chromium.launch(executable_path=chrome(),
                                      args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            except Exception as e:                       # no browser installed
                pytest.skip(f"no Chromium for Playwright: {e}")
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script("Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'})")   # Mac keys
            pg = ctx.new_page()
            pg.set_default_timeout(90000)          # generous: the server may share the CPU with other tests
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
            pg.wait_for_function("window.ofApp && window.ofApp.state && window.ofApp.state.scene")
            yield pg, f, errors
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()


def fill(pg, values):
    for key, v in values.items():
        pg.evaluate("""([k, v]) => {
            const el = document.querySelector(`#dialog [data-key="${k}"]`);
            if (!el) throw new Error("no field " + k);
            el.value = v; el.dispatchEvent(new Event("input")); el.dispatchEvent(new Event("change"));
        }""", [key, v])


@pytest.mark.slow
def test_every_tool_writes_a_line_that_builds(page):
    pg, f, errors = page
    for tool, values, op in STEPS:
        before = f.read_text()
        pg.evaluate("(id) => window.ofApp.openTool(id)", tool)
        pg.wait_for_selector("#dialog:not([hidden]) #dlg-ok")
        fill(pg, values)
        pg.wait_for_timeout(150)
        pg.click("#dlg-ok")
        try:
            pg.wait_for_function("document.getElementById('dialog').hidden", timeout=15000)
        except Exception:
            raise AssertionError(f"{tool}: dialog stayed open: {pg.inner_text('#dialog')}")
        text = f.read_text()
        assert text != before, tool
        last = text.strip().splitlines()[-1]
        assert re.match(rf"(\w+ = )?{op}\(", last), (tool, last)
    r = Engine().run_file(str(f))
    assert r.ok, r.report()
    assert not errors, errors


def test_parameters_panel_round_trip(page):
    pg, f, errors = page
    pg.click("#params-btn")
    pg.fill(".pp-add [data-col=name]", "wall")
    pg.fill(".pp-add [data-col=expr]", "2")
    pg.fill(".pp-add [data-col=comment]", "outer wall")
    pg.click("#pp-add")
    pg.wait_for_function("document.querySelector('tr[data-row=\"wall\"] [data-col=comment]')?.value === 'outer wall'")
    assert '"wall": 2 * mm,  # outer wall' in f.read_text()
    inp = pg.locator('tr[data-row="wall"] [data-col=name]')
    inp.fill("skin")
    inp.press("Enter")
    pg.wait_for_selector('tr[data-row="skin"]')
    pg.locator('tr[data-row="skin"] [data-col=expr]').fill("P.skin_bad +")
    pg.locator('tr[data-row="skin"] [data-col=expr]').press("Enter")
    pg.wait_for_function("document.querySelector('.pp-err').textContent.length > 0")
    pg.keyboard.press("Escape")
    assert pg.is_hidden("#params-panel")
    pg.keyboard.press("Control+Shift+KeyV")
    assert pg.is_visible("#params-panel")
    pg.click('[data-del="skin"]')
    pg.wait_for_function("!document.querySelector('tr[data-row=\"skin\"]')")
    assert "skin" not in f.read_text()
    assert not errors, errors



def test_shortcut_hint_opacity_and_selection_readout(page):
    pg, f, errors = page
    # every tool with a shortcut shows it in its dialog
    pg.evaluate("() => window.ofApp.openTool('box')")
    pg.wait_for_selector("#dialog .dlg-key")
    assert "Control+B" in pg.inner_text("#dialog .dlg-key") or "Alt+B" in pg.inner_text("#dialog .dlg-key")
    pg.evaluate("() => window.ofApp.closeDialog()")
    # opacity from the body menu, remembered for the part
    pg.evaluate("() => window.ofApp.bodyMenu('plate', 400, 300)")
    pg.wait_for_selector(".ctx-opacity input")
    pg.evaluate("() => { const i = document.querySelector('.ctx-opacity input'); i.value = 40; i.dispatchEvent(new Event('input')); }")
    assert pg.evaluate("() => window.ofApp.vp.opacity.get('plate')") == pytest.approx(0.4)
    assert pg.evaluate("() => window.ofApp.vp.faceMeshes.filter(m => m.userData.body === 'plate').every(m => Math.abs(m.material.opacity - 0.4) < 1e-6)")
    # the selection readout: select a face and the body
    pg.evaluate("""() => { const a = window.ofApp; a.selection = [{kind: 'face', body: 'plate', ref: 'plate.face("+Z")', label: '+Z', key: 'face:plate.face("+Z")'}];
        a.renderSelection(); }""")
    pg.wait_for_function("!document.getElementById('selinfo').hidden")
    text = pg.inner_text("#selinfo")
    assert "Flat face" in text.title() or "FLAT FACE" in text.upper()
    assert "1200.0 mm²" in text or "1200.000 mm²" in text            # the 40 x 30 top
    assert not errors, errors


def test_sketch_mode_draws_constrains_and_writes_a_sketch(page):
    """Sketch mode with real clicks: a rectangle on the plate's top, a dimension typed on
    a line, a circle inside, a constraint from the panel; finishing writes one sketch(...)
    statement that builds, and editing it reads it back."""
    pg, f, errors = page
    pg.evaluate("""() => { const a = window.ofApp; const face = a.vp.data.bodies.find(b => b.id === 'plate');
        const hit = a.vp.faceMeshes.find(m => m.userData.body === 'plate' && m.userData.label === '+Z').userData;
        a.selection = [{kind: 'face', body: 'plate', ref: hit.ref, label: '+Z', entry: hit.face, point: [0, 0, 10], key: 'face:' + hit.ref}];
        a.openTool('sketch'); }""")
    pg.wait_for_selector("#dialog.sk-panel #sk-ok")
    # the view swings round to face the plane; the empty sketch is solved once
    pg.wait_for_function("window.ofApp.dialog.solved && !window.ofApp.vp.anim && !window.ofApp.dialog.busy")
    box = pg.locator("#view").bounding_box()

    def at(x, y):
        sx, sy = pg.evaluate("([x, y]) => window.ofApp.dialog.screen([x, y])", [x, y])
        return box["x"] + sx, box["y"] + sy

    def click(x, y):
        X, Y = at(x, y)
        pg.mouse.move(X, Y)
        pg.mouse.click(X, Y)

    def settle():
        pg.wait_for_timeout(50)
        pg.wait_for_function("window.ofApp.dialog && window.ofApp.dialog.solved && !window.ofApp.dialog.dragBusy && !window.ofApp.dialog.busy")

    pg.keyboard.press("r")
    click(-12, -8)
    click(12, 8)
    settle()
    m = pg.evaluate("() => window.ofApp.dialog.model")
    assert len(m["curves"]) == 4 and len(m["cons"]) == 4          # four lines, horizontal/vertical
    # dimension the bottom line: select it, D, type 30
    l1 = pg.evaluate("() => Object.keys(window.ofApp.dialog.model.curves)[0]")
    pg.keyboard.press("s")
    a, b = pg.evaluate("(n) => window.ofApp.dialog.curveGeom(n)", l1)
    click((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    assert pg.evaluate("() => window.ofApp.dialog.sel") == ["c:" + l1]
    pg.keyboard.press("d")
    pg.wait_for_selector(".sk-ask input")
    pg.fill(".sk-ask input", "30")
    pg.press(".sk-ask input", "Enter")
    settle()
    a, b = pg.evaluate("(n) => window.ofApp.dialog.curveGeom(n)", l1)
    assert abs(abs(b[0] - a[0]) - 30) < 1e-6
    assert pg.locator(".sk-dim").count() == 1
    # a circle in the middle, then make it concentric... with itself? no: fix its centre
    pg.keyboard.press("c")
    click(0, 0)
    click(3, 0)
    settle()
    circle = [n for n, c in pg.evaluate("() => window.ofApp.dialog.model.curves").items() if c[0] == "circle"]
    assert len(circle) == 1
    pg.keyboard.press("s")
    click(0, 3)                       # on the rim
    pg.click('[data-rule="fix"]')
    settle()
    assert ["fix", circle[0]] in pg.evaluate("() => window.ofApp.dialog.model.cons")
    st = pg.inner_text(".sk-status")
    assert "profile region" in st and "1 profile region" in st, st
    # undo inside the sketch takes the fix away again, redo puts it back
    pg.keyboard.press("Meta+z")
    settle()
    assert ["fix", circle[0]] not in pg.evaluate("() => window.ofApp.dialog.model.cons")
    pg.keyboard.press("Meta+Shift+z")
    settle()
    assert ["fix", circle[0]] in pg.evaluate("() => window.ofApp.dialog.model.cons")
    # project an edge of the plate: it comes in fixed, with its ends as points
    pg.keyboard.press("j")
    pg.evaluate("""() => { const d = window.ofApp.dialog; d.click({ clientX: 0, clientY: 0, shiftKey: false }); }""")   # nothing there
    edge = pg.evaluate("""() => { const a = window.ofApp; const l = a.vp.edgeLines.find(x => x.userData.body === 'plate' && x.userData.label === '+Y+Z');
        const p = l.userData.points; const m = [(p[0] + p[3]) / 2, (p[1] + p[4]) / 2, (p[2] + p[5]) / 2];
        const r = a.vp.canvas.getBoundingClientRect(); const s = a.vp.toScreen(m); return [r.left + s[0], r.top + s[1]]; }""")
    pg.mouse.move(*edge)
    pg.mouse.click(*edge)
    settle()
    proj = [n for n, c in pg.evaluate("() => window.ofApp.dialog.model.curves").items() if c[0] == "project"]
    assert len(proj) == 1, pg.evaluate("() => window.ofApp.dialog.model.curves")
    info = pg.evaluate("(n) => window.ofApp.dialog.solved.curves[n]", proj[0])
    assert info["kind"] == "line" and info["fixed"] and info["pts"] == [proj[0] + ".start", proj[0] + ".end"]
    pg.keyboard.press("Meta+z")                         # take it out again: the rest of the test doesn't need it
    settle()
    # drag the far corner up: the rectangle stays a rectangle, 30 long, and gets taller
    pg.keyboard.press("s")
    corner = pg.evaluate("""() => { const d = window.ofApp.dialog; const g = d.curveGeom('l2'); return g[1]; }""")
    X, Y = at(*corner)
    pg.mouse.move(X, Y)
    pg.mouse.down()
    for k in range(1, 6):
        pg.mouse.move(X, Y - 8 * k)
        pg.wait_for_timeout(120)
    pg.mouse.up()
    settle()
    pts = pg.evaluate("() => window.ofApp.dialog.model.points")
    ys = sorted({round(p[1], 6) for p in pts.values()})
    assert ys[-1] > corner[1] + 1, (corner, pts)
    l1g = pg.evaluate("() => window.ofApp.dialog.curveGeom('l1')")
    assert abs(abs(l1g[1][0] - l1g[0][0]) - 30) < 1e-6 and abs(l1g[1][1] - l1g[0][1]) < 1e-6
    pg.click("#sk-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    text = f.read_text()
    assert "sketch1 = sketch(" in text and '("length", "l1", 30)' in text, text
    r = Engine().run_file(str(f))
    assert r.ok, r.report()
    # edit it: the statement comes back apart, and finishing again rewrites it in place
    pg.evaluate("() => window.ofApp.editFeature('sketch1')")
    pg.wait_for_function("window.ofApp.dialog && window.ofApp.dialog.solved && Object.keys(window.ofApp.dialog.model.curves).length === 5")
    pg.click("#sk-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    assert f.read_text().count("sketch(") == 1
    assert not errors, errors


def test_section_view_cuts_the_view_only(page):
    pg, f, errors = page
    before = f.read_text()
    pg.evaluate("() => window.ofApp.openTool('section')")
    pg.wait_for_selector("#dialog:not([hidden]) #sec-ok")
    sec = pg.evaluate("() => window.ofApp.vp.section")
    assert sec and sec["n"] == [0, -1, 0]                                   # world XZ, the front half cut away
    assert pg.evaluate("() => window.ofApp.vp.faceMeshes.every(m => m.material.clippingPlanes.length === 1)")
    assert pg.evaluate("() => window.ofApp.vp._caps.length > 0")
    pg.click('[data-world="XY"]')
    pg.fill("#sec-offset", "-2")
    sec = pg.evaluate("() => window.ofApp.vp.section")
    assert sec["n"] == [0, 0, 1] and sec["offset"] == -2
    pg.click("#sec-flip")
    assert pg.evaluate("() => window.ofApp.vp.section.n") == [0, 0, -1]
    # a click on a flat face cuts parallel to it, through the middle of the model
    pg.evaluate("""() => { const a = window.ofApp; const m = a.vp.faceMeshes.find(x => x.userData.body === 'plate' && x.userData.label === '+X');
        const d = a.dialog; d.fromFace(m.userData.face, 'plate', '+X', [40, 15, 5]); d.renderPanel(); d.update(); }""")
    assert pg.evaluate("() => window.ofApp.vp.section.n") == [1, 0, 0]
    pg.click("#sec-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    assert pg.is_visible("#t-section")
    # picking ignores what the cut hides; the chip turns the cut off
    pg.click("#t-section")
    assert pg.evaluate("() => window.ofApp.vp.section") is None and pg.is_hidden("#t-section")
    assert pg.evaluate("() => window.ofApp.vp.faceMeshes.every(m => m.material.clippingPlanes.length === 0)")
    # Esc (cancel) puts back what was there before
    pg.evaluate("() => window.ofApp.openTool('section')")
    pg.wait_for_selector("#dialog:not([hidden]) #sec-ok")
    pg.keyboard.press("Escape")
    assert pg.evaluate("() => window.ofApp.vp.section") is None
    assert f.read_text() == before and not errors, errors
