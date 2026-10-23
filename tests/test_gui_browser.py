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

# A browser test that runs out of time mid-call leaves Playwright unable to close the browser,
# which hangs the whole run; they get a limit they won't hit on a busy machine.
pytestmark = pytest.mark.limit(90)

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
    ("scale", {"body": "body5", "factor.0": "2"}, "scale"),
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
    ws.support_dir, ws.parts_dir = tmp_path / "support", tmp_path / "Parts"     # not the real home folder
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
            pg.set_default_timeout(30000)          # well inside the 90 s test limit, so a wait fails cleanly
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


def test_timeline_marker_drag_delete_key_and_body_highlight(page):
    pg, f, errors = page
    for i in range(3):
        pg.evaluate(f"() => window.ofApp.action('/api/apply', {{op: 'box', args: {{length: '4', width: '4', height: '4', at: 'world.point({60 + 10 * i}, 0, 0)'}}}})")
    pg.wait_for_function("window.ofApp.state.desc.features.length === 4")
    pg.evaluate("() => window.ofApp.refresh()")          # settled: no redraw of the timeline mid-drag
    pg.wait_for_timeout(200)
    marker = pg.locator("#timeline .marker")
    assert marker.count() == 1 and "at-end" in marker.get_attribute("class")
    # drag the marker back to just after body1 (again if a redraw of the timeline got in the way)
    for attempt in range(3):
        pg.evaluate("() => document.querySelector('#timeline .marker').scrollIntoView({block: 'nearest', inline: 'center'})")
        pg.wait_for_timeout(150)
        m, c = pg.evaluate("""() => [document.querySelector('#timeline .marker'), document.querySelector('#timeline .chip[data-name="body1"]')]
            .map(e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; })""")
        pg.mouse.move(m["x"] + m["width"] / 2, m["y"] + m["height"] / 2)
        pg.wait_for_timeout(50)
        pg.mouse.down()
        for k in range(1, 11):
            pg.mouse.move(m["x"] + m["width"] / 2 + (c["x"] + c["width"] + 2 - m["x"] - m["width"] / 2) * k / 10, m["y"] + m["height"] / 2)
            pg.wait_for_timeout(30)
        pg.mouse.up()
        try:
            pg.wait_for_function("window.ofApp.state.marker === 'body1'", timeout=5000)
            break
        except pw.TimeoutError:
            continue
    pg.wait_for_function("window.ofApp.state.marker === 'body1'")
    order = pg.evaluate("() => [...document.querySelectorAll('#timeline li')].map(l => l.classList.contains('marker') ? '|' : l.dataset.name)")
    assert order == ["plate", "body1", "|", "body2", "body3"]
    assert pg.evaluate("() => window.ofApp.state.scene.bodies.map(b => b.id)") == ["plate", "body1"]
    # pick a step and press Delete: it goes, and the model stays rolled back
    pg.click('#timeline .chip[data-name="body1"]')
    assert "picked" in pg.locator('#timeline .chip[data-name="body1"]').get_attribute("class")
    pg.keyboard.press("Delete")
    pg.wait_for_function("!window.ofApp.state.desc.features.some(f => f.name === 'body1')")
    assert "body1 =" not in f.read_text() and pg.evaluate("() => window.ofApp.state.marker") == "plate"
    # a click on the marker goes back to the end
    pg.click("#timeline .marker")
    pg.wait_for_function("window.ofApp.state.marker === null")
    # selecting a face marks its body in the list; hovering in the view marks the row too
    pg.evaluate("""() => { const a = window.ofApp; const m = a.vp.faceMeshes.find(x => x.userData.body === 'body2');
        a.selection = [{kind: 'face', body: 'body2', ref: m.userData.ref, label: m.userData.label, key: 'face:' + m.userData.ref}]; a.renderSelection(); }""")
    assert "sel-part" in pg.locator('.bodies li[data-body="body2"]').get_attribute("class")
    pg.click('.bodies li[data-body="plate"]')
    assert "sel" in pg.locator('.bodies li[data-body="plate"]').get_attribute("class").split()
    assert "sel-part" not in (pg.locator('.bodies li[data-body="body2"]').get_attribute("class") or "")
    assert not errors, errors


def test_reused_names_banner_fixes_the_file(page):
    pg, f, errors = page
    from test_scriptedit import REUSED
    pg.evaluate("(t) => window.ofApp.action('/api/script', {text: t})", REUSED)      # as if typed in the script pane
    pg.wait_for_selector("#banner button", timeout=15000)
    assert "used for two steps" in pg.inner_text("#banner")
    pg.click("#banner button")
    pg.wait_for_function("window.ofApp.state.desc.shadowed.length === 0")
    assert pg.is_hidden("#banner") and "extrude1 = extrude(" in f.read_text()
    assert not errors, errors


def test_origin_planes_are_clickable_and_scale_can_be_non_uniform(page):
    pg, f, errors = page
    # the Origin toggle shows the three planes
    pg.click("#t-origin")
    pg.wait_for_function("window.ofApp.vp.originGroup !== null")
    assert pg.evaluate("() => window.ofApp.vp.originGroup.children.filter(o => o.userData.kind === 'origin').map(o => o.userData.plane)") == ["XY", "XZ", "YZ"]
    pg.click("#t-origin")
    pg.wait_for_function("window.ofApp.vp.originGroup === null")
    # sketch mode offers them: click the XZ plane where nothing is in front of it
    pg.evaluate("() => window.ofApp.vp.viewTo([1, -1.2, 0.8], true)")
    pg.wait_for_function("!window.ofApp.vp.anim")
    pg.evaluate("() => window.ofApp.openTool('sketch')")
    pg.wait_for_function("window.ofApp.vp.originGroup !== null")
    box = pg.locator("#view").bounding_box()
    sx, sy = pg.evaluate("() => window.ofApp.vp.toScreen([-8, 0, 12])")
    pg.mouse.move(box["x"] + sx, box["y"] + sy)
    assert "XZ" in pg.inner_text("#tip")
    pg.mouse.click(box["x"] + sx, box["y"] + sy)
    pg.wait_for_function("window.ofApp.dialog && window.ofApp.dialog.frame")
    assert pg.evaluate("() => window.ofApp.dialog.frame.z") == [0, -1, 0]
    assert pg.evaluate("() => window.ofApp.dialog.at") == 'world.anchor("XZ")'
    pg.keyboard.press("Escape")
    pg.keyboard.press("Escape")
    # a selected origin plane feeds a primitive's position
    pg.evaluate("""() => { const a = window.ofApp; a.closeDialog();
        a.selection = [{kind: 'origin', plane: 'YZ', ref: 'world.anchor("YZ")', frame: {o: [0,0,0], x: [0,1,0], y: [0,0,1], z: [1,0,0]}, key: 'origin:YZ'}];
        a.openTool('cylinder'); }""")
    assert pg.input_value('#dialog [data-key="at"]') == 'world.anchor("YZ")'
    pg.evaluate("() => window.ofApp.closeDialog()")
    # non-uniform scale: unlock X/Y/Z and give each its own factor
    pg.evaluate("() => { const a = window.ofApp; a.selection = [{kind: 'body', body: 'plate', ref: 'plate', key: 'body:plate'}]; a.openTool('scale'); }")
    pg.wait_for_selector('#dialog [data-lock="factor"]')
    pg.fill('#dialog [data-key="factor.0"]', "2")
    assert pg.input_value('#dialog [data-key="factor.2"]') == "2"                  # uniform: they follow
    pg.click('#dialog [data-lock="factor"]')
    pg.fill('#dialog [data-key="factor.1"]', "1")
    pg.fill('#dialog [data-key="factor.2"]', "0.5")
    pg.click("#dlg-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    assert "factor=(2, 1, 0.5)" in f.read_text()
    assert not errors, errors


def test_sketch_curves_pick_as_edges_and_sweep_paths(page):
    pg, f, errors = page
    src = f.read_text() + (
        'path = sketch(at=world.anchor("XZ").offset(x=-30), points={"a": (0, 0), "b": (0, 20), "o": (10, 20), "c": (10, 30)},\n'
        '    curves={"up": ("line", "a", "b"), "bend": ("arc", "o", "c", "b")})\n'
        'sec = sketch(at=world.point(-30, 0, 0), points={"o": (0, 0)}, curves={"ring": ("circle", "o", 2), "el": ("ellipse", "o", 5, 3, 0, "construction")})\n')
    pg.evaluate("(t) => window.ofApp.action('/api/script', {text: t})", src)
    pg.wait_for_function("window.ofApp.state.scene.bodies.some(b => b.id === 'sec')")
    labels = pg.evaluate("() => window.ofApp.vp.edgeLines.filter(l => l.userData.body === 'path').map(l => l.userData.label)")
    assert labels == ["up", "bend"]
    assert pg.evaluate("() => window.ofApp.vp.edgeLines.find(l => l.userData.body === 'sec' && l.userData.label === 'el').material.type") == "LineDashedMaterial"
    # in Sweep, clicking the sketch's curves one by one builds the path from them
    pg.evaluate("() => window.ofApp.openTool('sweep')")
    pg.wait_for_selector("#dialog [data-key='path']")
    pg.evaluate("""() => { const d = window.ofApp.dialog; d.activate('profile');
        d.takeBody('sec'); d.activate('path');
        for (const name of ['up', 'bend']) {
            const l = window.ofApp.vp.edgeLines.find(x => x.userData.body === 'path' && x.userData.label === name);
            d.hoverHit = { kind: 'edge', body: 'path', ref: l.userData.ref, label: name };
            d.click({ clientX: 0, clientY: 0 });
        } }""")
    assert pg.input_value("#dialog [data-key='path']") == '[path.edge("up"), path.edge("bend")]'
    pg.click("#dlg-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    assert 'sweep(profile=sec, path=[path.edge("up"), path.edge("bend")])' in f.read_text()
    assert pg.evaluate("() => window.ofApp.state.scene.timeline.slice(-1)[0].status") == "ok"
    assert not errors, errors


def test_sketch_type_in_boxes_inspector_and_contents(page):
    """Drawing with typed sizes (Tab between the boxes, Enter places), then the side panel:
    the inspector edits the selected item, the contents list selects, highlights and deletes,
    and the Text tab rewrites the whole sketch."""
    pg, f, errors = page
    pg.evaluate("""() => { const a = window.ofApp;
        const hit = a.vp.faceMeshes.find(m => m.userData.body === 'plate' && m.userData.label === '+Z').userData;
        a.selection = [{kind: 'face', body: 'plate', ref: hit.ref, label: '+Z', entry: hit.face, point: [0, 0, 10], key: 'face:' + hit.ref}];
        a.openTool('sketch'); }""")
    pg.wait_for_selector("#dialog.sk-panel #sk-ok")
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
        pg.wait_for_function("window.ofApp.dialog && window.ofApp.dialog.solved && !window.ofApp.dialog.busy")

    model = lambda: pg.evaluate("() => window.ofApp.dialog.model")
    # a rectangle: one corner, then type 24, Tab, 14, Enter
    pg.keyboard.press("r")
    click(-10, -6)
    pg.mouse.move(*at(3, 2))
    pg.wait_for_selector(".sk-hud input")
    pg.keyboard.type("24")                      # goes into the first box
    assert pg.input_value('.sk-hud input[data-hud="0"]') == "24"
    pg.keyboard.press("Tab")
    pg.keyboard.type("14")
    pg.keyboard.press("Enter")
    settle()
    m = model()
    lines = [n for n, c in m["curves"].items() if c[0] == "line"]
    assert len(lines) == 4
    assert ["length", lines[0], 24] in m["cons"] and ["length", lines[1], 14] in m["cons"], m["cons"]
    a, b = pg.evaluate("(n) => window.ofApp.dialog.curveGeom(n)", lines[0])
    assert abs(b[0] - a[0]) == pytest.approx(24) and abs(b[1] - a[1]) < 1e-6
    # a circle with its diameter typed
    pg.keyboard.press("c")
    click(2, 1)
    pg.mouse.move(*at(4, 1))
    pg.keyboard.type("5")
    pg.keyboard.press("Enter")
    settle()
    circle = next(n for n, c in model()["curves"].items() if c[0] == "circle")
    assert ["diameter", circle, 5] in model()["cons"]
    # the contents list has every curve; hovering a row highlights it, clicking selects it
    pg.keyboard.press("Escape")
    pg.keyboard.press("s")
    rows = pg.locator(".sk-list li[data-key]")
    assert rows.count() == 5
    pg.hover(f'.sk-list li[data-key="c:{circle}"]')
    assert pg.evaluate("() => window.ofApp.dialog.hoverKey") == "c:" + circle
    pg.click(f'.sk-list li[data-key="c:{circle}"] .nm')
    assert pg.evaluate("() => window.ofApp.dialog.sel") == ["c:" + circle]
    # the inspector shows the diameter (held by its dimension) and changes it
    inp = pg.locator('.sk-insp input[data-f="diameter"]')
    assert inp.input_value() == "5"
    assert pg.locator(".sk-insp .sk-unlock").count() == 1
    inp.fill("8")
    inp.press("Enter")
    settle()
    assert ["diameter", circle, 8] in model()["cons"]
    r = pg.evaluate("(n) => window.ofApp.dialog.solved.curves[n].radius", circle)
    assert r == pytest.approx(4)
    # a line's length typed in the inspector replaces its dimension; a point moves by its X
    pg.click(f'.sk-list li[data-key="c:{lines[2]}"] .nm')
    pg.fill('.sk-insp input[data-f="length"]', "24")             # same as its opposite side: fine
    pg.press('.sk-insp input[data-f="length"]', "Enter")
    settle()
    assert ["length", lines[2], 24] in model()["cons"]
    # constraints tab: values are editable in the list, and × deletes
    pg.click('[data-tab="cons"]')
    i = next(k for k, c in enumerate(model()["cons"]) if c[:2] == ["length", lines[2]])
    pg.click(f'.sk-list li[data-key="k:{i}"] .x', force=True)
    settle()
    assert not any(c[:2] == ["length", lines[2]] for c in model()["cons"])
    i = next(k for k, c in enumerate(model()["cons"]) if c[:2] == ["length", lines[0]])
    pg.fill(f'input[data-con="{i}"]', "30")
    pg.press(f'input[data-con="{i}"]', "Enter")
    settle()
    a, b = pg.evaluate("(n) => window.ofApp.dialog.curveGeom(n)", lines[0])
    assert abs(b[0] - a[0]) == pytest.approx(30)
    # points tab lists the points
    pg.click('[data-tab="points"]')
    assert pg.locator(".sk-list li[data-key^='p:']").count() >= 5
    # the Text tab: the whole sketch; an edit there applies (here: the circle becomes an ellipse)
    pg.click('[data-tab="text"]')
    t = pg.input_value(".sk-text")
    assert t.startswith("points={") and "curves={" in t and "constraints=[" in t
    t2 = t.replace(f'"{circle}": ("circle", ', f'"{circle}": ("ellipse", ').replace(f'("diameter", "{circle}", 8)', "")
    t2 = re.sub(rf'("{circle}": \("ellipse", "[^"]+"), [\d.]+\)', r'\1, 6, 3)', t2)
    t2 = re.sub(r",\s*,", ",", t2).replace("[,", "[").replace(",\n]", "\n]")
    pg.fill(".sk-text", t2)
    pg.click("[data-apply-text]")
    settle()
    c = model()["curves"][circle]
    assert c[0] == "ellipse" and c[2:4] == [6, 3], (c, t2)
    # a broken text says why and changes nothing
    pg.fill(".sk-text", "points={")
    pg.click("[data-apply-text]")
    pg.wait_for_selector(".toast.err")
    assert model()["curves"][circle][0] == "ellipse"
    # the ellipse's half-widths are editable in the inspector
    pg.click('[data-tab="curves"]')
    pg.click(f'.sk-list li[data-key="c:{circle}"] .nm')
    pg.fill('.sk-insp input[data-f="rx"]', "7")
    pg.press('.sk-insp input[data-f="rx"]', "Enter")
    settle()
    assert model()["curves"][circle][2] == 7
    pg.click("#sk-ok")
    pg.wait_for_function("document.getElementById('dialog').hidden")
    r = Engine().run_file(str(f))
    assert r.ok, r.report()
    assert '"ellipse"' in f.read_text()
    assert not errors, errors


def test_view_attributes_lists_points_edges_faces_and_highlights(page):
    """Right-click a body: View attributes lists its anchors and face points, edges and faces;
    hovering highlights in the view, clicking selects and copies the reference. A sketch lists
    its curves as edges and its points as anchors."""
    pg, f, errors = page
    src = START + 's9 = sketch(at=plate.face("+Z"), points={"a": (0, 0), "b": (8, 0)}, curves={"l1": ("line", "a", "b"), "c1": ("circle", "a", 3)})\n'
    pg.evaluate("(t) => window.ofApp.action('/api/script', {text: t})", src)
    pg.wait_for_function("window.ofApp.vp.body('s9')")
    pg.click('.bodies li[data-body="plate"]', button="right")
    pg.click("#ctx >> text=View attributes")
    pg.wait_for_selector("#attrs:not([hidden]) .at-list li[data-key]")
    tabs = pg.inner_text("#attrs .at-tabs")
    assert "Points" in tabs and "Edges" in tabs and "Faces" in tabs
    assert pg.locator('#attrs li[data-key="a:+Z"]').count() == 1
    assert pg.locator("#attrs li.at-group").count() == 2               # anchors, then points on faces
    # hovering a point marks it in the view
    pg.hover('#attrs li[data-key="a:+Z"]')
    assert pg.evaluate("() => window.ofApp.vp.pointGroup.children.length") == 1
    # faces: hover highlights, click selects
    pg.click('#attrs [data-tab="faces"]')
    assert pg.locator("#attrs .at-list li[data-key]").count() == 6
    assert "plane" in pg.inner_text('#attrs li[data-key="f:+Z"]')
    pg.hover('#attrs li[data-key="f:+Z"]')
    assert pg.evaluate("() => window.ofApp.vp.highlight.hover") == 'face:plate.face("+Z")'
    pg.click('#attrs li[data-key="f:+Z"] .nm')
    assert pg.evaluate("() => window.ofApp.selection.map(s => s.key)") == ['face:plate.face("+Z")']
    # edges, filtered
    pg.click('#attrs [data-tab="edges"]')
    assert pg.locator("#attrs .at-list li[data-key]").count() == 12
    pg.fill("#attrs .at-filter", "+Z")
    assert pg.locator("#attrs .at-list li[data-key]").count() == 4
    pg.hover("#attrs .at-list li[data-key] >> nth=0")
    assert pg.evaluate("() => window.ofApp.vp.highlight.hover").startswith("edge:plate.edge(")
    # a sketch: its curves are its edges, its points are anchors
    pg.click('.chip[data-name="s9"]', button="right")
    pg.click("#ctx >> text=View attributes")
    pg.wait_for_function("document.querySelector('#attrs .at-head b').textContent === 's9'")
    pg.click('#attrs [data-tab="edges"]')
    keys = pg.eval_on_selector_all("#attrs .at-list li[data-key]", "els => els.map(e => e.dataset.key)")
    assert sorted(keys) == ["e:c1", "e:l1"]
    pg.click('#attrs [data-tab="points"]')
    assert pg.locator('#attrs li[data-key="a:a"]').count() == 1 and pg.locator('#attrs li[data-key="a:b"]').count() == 1
    pg.click('#attrs li[data-key="a:b"] .nm')
    assert pg.evaluate("() => window.ofApp.vp.pointGroup.children.length") == 1
    # it follows rebuilds, and closes with Escape
    pg.keyboard.press("Escape")
    pg.keyboard.press("Escape")
    assert pg.locator("#attrs").is_hidden()
    assert not errors, errors


def test_documents_panel_copies_renames_and_opens(page):
    """Click the file name: the Documents panel. Save a copy (staying put), rename the open
    part, open the copy from the list, make a new part."""
    pg, f, errors = page
    folder = f.parent
    (folder / "other.ofs").write_text(START)
    pg.click("#file")
    pg.wait_for_selector("#docs-panel .dc-row")
    names = pg.eval_on_selector_all("#docs-panel .dc-row .nm", "els => els.map(e => e.textContent)")
    assert sorted(names) == ["gui", "other"]
    assert pg.locator("#docs-panel .dc-row.cur .nm").inner_text() == "gui"
    # a copy, staying in this part
    pg.fill("#dc-copy", "gui v2")
    pg.uncheck("#dc-switch")
    pg.click("#dc-saveas")
    pg.wait_for_function("document.querySelectorAll('#docs-panel .dc-row').length === 3")
    assert (folder / "gui v2.ofs").read_text() == f.read_text()
    assert pg.evaluate("() => window.ofApp.state.file").endswith("gui.ofs")
    # rename the open part
    pg.fill("#dc-name", "plate part")
    pg.click("#dc-rename")
    pg.wait_for_function("window.ofApp.state.file.endsWith('plate part.ofs')")
    assert not f.exists() and (folder / "plate part.ofs").exists()
    pg.wait_for_function("document.querySelector('#docs-panel .dc-row.cur .nm')?.textContent === 'plate part'")
    assert pg.inner_text("#file") == "plate part.ofs"
    pg.keyboard.press("Escape")
    assert pg.locator("#docs-panel").is_hidden()
    # edits carry on into the renamed file
    pg.evaluate("() => window.ofApp.action('/api/param', {name: 'wall', value: '2'})")
    pg.wait_for_function("window.ofApp.state.text.includes('wall')")
    assert "wall" in (folder / "plate part.ofs").read_text()
    # open the copy from the list: the panel closes, the copy is open
    pg.click("#file")
    pg.fill("#docs-panel .dc-filter", "v2")
    pg.wait_for_function("document.querySelectorAll('#docs-panel .dc-row').length === 1")
    pg.click("#docs-panel .dc-row")
    pg.wait_for_function("window.ofApp.state.file.endsWith('gui v2.ofs') && document.getElementById('docs-panel').hidden")
    # a new part next to it; a name that's taken says so
    pg.keyboard.press("Meta+Shift+o")
    pg.wait_for_selector("#docs-panel #dc-new")
    pg.fill("#dc-new", "other")
    pg.press("#dc-new", "Enter")
    pg.wait_for_function("document.querySelector('#docs-panel .dc-err').textContent.includes('already exists')")
    pg.fill("#dc-new", "spacer")
    pg.press("#dc-new", "Enter")
    pg.wait_for_function("window.ofApp.state.file.endsWith('spacer.ofs')")
    assert (folder / "spacer.ofs").exists()
    pg.keyboard.press("Meta+s")                          # nothing to do: it says it's saved
    pg.wait_for_selector(".toast:not([hidden])")
    assert "Saved" in pg.inner_text(".toast")
    assert not errors, errors


def test_import_tool_copies_the_file_and_builds_it(page, tmp_path):
    """Import: Choose… sends the file next to the part, the field gets its name, OK writes
    import_file("...") and the body appears with its named faces."""
    pg, f, errors = page
    from openfusion.export import export_step
    r = Engine().run_source("from openfusion import *\nb = box(length=12, width=8, height=5)\n")
    src = tmp_path / "elsewhere"
    src.mkdir()
    export_step([r.bodies["b"]], str(src / "block.step"))
    pg.evaluate("() => window.ofApp.openTool('import_file')")
    pg.wait_for_selector('#dialog [data-file="path"]')
    with pg.expect_file_chooser() as fc:
        pg.click('#dialog [data-file="path"]')
    fc.value.set_files(str(src / "block.step"))
    pg.wait_for_function("document.querySelector('#dialog [data-key=path]').value === 'block.step'")
    assert (f.parent / "block.step").read_bytes() == (src / "block.step").read_bytes()
    pg.wait_for_function("document.getElementById('dlg-stmt').textContent.includes('import_file(path=\"block.step\")')")
    pg.click("#dlg-ok")
    pg.wait_for_function("window.ofApp.vp.body('body1')")
    assert 'body1 = import_file(path="block.step")' in f.read_text()
    b = pg.evaluate("() => window.ofApp.vp.body('body1').faces.map(f => f.label).sort()")
    assert b == sorted(["+X", "-X", "+Y", "-Y", "+Z", "-Z"])
    # the same file again is not copied twice; a different one with that name gets -2
    s = pg.evaluate("() => window.ofApp.vp.body('body1').faces.length")
    assert s == 6
    import base64
    data = base64.b64encode(b"ISO-10303-21; other").decode()
    out = pg.evaluate("(d) => fetch('/api/upload', {method: 'POST', body: JSON.stringify({name: 'block.step', data: d})}).then(r => r.json())", data)
    assert out["name"] == "block-2.step" and out["renamed"]
    same = base64.b64encode((src / "block.step").read_bytes()).decode()
    out = pg.evaluate("(d) => fetch('/api/upload', {method: 'POST', body: JSON.stringify({name: 'block.step', data: d})}).then(r => r.json())", same)
    assert out["name"] == "block.step"
    out = pg.evaluate("() => fetch('/api/upload', {method: 'POST', body: JSON.stringify({name: 'x.exe', data: ''})}).then(r => r.json())")
    assert "Import takes" in out["error"]
    assert not errors, errors


def test_an_odd_shaped_face_offers_its_corners_and_lines_its_grid_up_with_one(page):
    """A face cut into an odd outline (here: notched by holes, more than 12 edges) offers every
    corner as a snap dot, and its grid (and the points clicked on it) start from a corner, not
    from the middle of the odd shape."""
    pg, f, errors = page
    src = (START + "".join(f"n{i} = cylinder(r=2, h=20, at=world.point({6 + 7 * i}, 0, -5))\n" for i in range(4))
           + "c = cut(plate, [n0, n1, n2, n3])\nt = extrude(plate.face(\"+Z\"), 5)\n")
    pg.evaluate("(t) => window.ofApp.action('/api/script', {text: t})", src)
    pg.wait_for_function("window.ofApp.vp.body('plate') && window.ofApp.vp.body('plate').faces.some(f => f.label === 't.end')")
    top = pg.evaluate("() => window.ofApp.vp.body('plate').faces.find(f => f.label === 't.end').snaps.map(s => s.kind)")
    assert top.count("corner") == 4 + 2 * 4, top
    # select that face, open Box: the start plane's base is a corner, and the grid sits on it
    pg.evaluate("""() => { const a = window.ofApp; const m = a.vp.faceMeshes.find(x => x.userData.body === 'plate' && x.userData.label === 't.end');
        a.selection = [{kind: 'face', body: 'plate', ref: m.userData.ref, label: 't.end', entry: m.userData.face, point: [20, 15, 15], key: 'face:' + m.userData.ref}];
        a.openTool('box'); }""")
    pg.wait_for_function("window.ofApp.dialog && window.ofApp.dialog.plane")
    base = pg.evaluate("() => window.ofApp.dialog.plane.base")
    assert ".corner(" in base["ref"], base
    assert base["frame"]["o"] == pytest.approx([0, 0, 15])       # the low-X, low-Y corner
    assert pg.evaluate("() => window.ofApp.vp.localFrame.o") == pytest.approx([0, 0, 15])
    # near the far corner, a click lands on it exactly: points are measured from the nearest
    # feature, and the grid moves there
    pg.evaluate("() => { window.ofApp.vp.frame('top'); }")
    pg.wait_for_timeout(300)
    pg.wait_for_function("!window.ofApp.vp.anim")
    xy = pg.evaluate("""() => { const a = window.ofApp; const r = a.vp.canvas.getBoundingClientRect();
        const s = a.vp.toScreen([0.4, 29.7, 15]); return [r.left + s[0], r.top + s[1]]; }""")
    pg.mouse.move(*xy)
    pg.mouse.move(xy[0] + 1, xy[1])
    pg.wait_for_timeout(100)
    c = pg.evaluate("() => { const d = window.ofApp.dialog; return d.cands[0]; }")
    assert c["kind"] == "free" and re.search(r'\.corner\(\d+\)$', c["ref"]), c["ref"]
    assert c["p"] == pytest.approx([0, 30, 15])
    assert pg.evaluate("() => window.ofApp.vp.localFrame.o") == pytest.approx([0, 30, 15])
    assert not errors, errors
