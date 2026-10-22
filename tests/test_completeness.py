"""Checks that the tests (and the GUI) keep up with the API: every public function, every
operation, every reference method, every server route and every command is exercised
somewhere in the test suite, and every GUI tool field is a real parameter of its operation."""
import inspect
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import openfusion
from openfusion import refs, scriptedit

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = "\n".join(p.read_text() for p in (ROOT / "tests").glob("test_*.py") if p.name != "test_completeness.py")
EXAMPLES = "\n".join(p.read_text() for p in (ROOT / "examples").glob("*.ofs"))


def used(name, text=TESTS):
    return re.search(r"(?:(?<![\w.])|(?<=\\n))" + re.escape(name) + r"\(", text) is not None


def test_every_public_name_is_exercised():
    missing = [n for n in openfusion.__all__ if n not in ("P", "mm", "cm", "m", "inch", "deg", "rad", "world")
               and not used(n)]
    assert not missing, f"no test calls {missing}"
    for unit in ("mm", "cm", "inch", "deg"):
        assert re.search(rf"\b{unit}\b", TESTS)


def test_every_operation_is_exercised_and_documented():
    for op in sorted(scriptedit.OPS):
        assert used(op), f"no test calls {op}()"
        fn = getattr(openfusion, op)
        assert (getattr(fn, "__wrapped__", fn).__doc__ or "").strip(), f"{op} has no docstring"
        assert scriptedit.op_params(op), op


def test_every_reference_method_is_exercised():
    classes = [refs.Handle, refs.FaceRef, refs.FrameRef, refs.Ref, refs._World]
    for cls in classes:
        for name, fn in inspect.getmembers(cls):
            if name.startswith("_") or name.endswith("_") or name in ("sig", "owner", "describe", "resolve", "frame", "members", "body_id"):
                continue
            assert re.search(r"\." + name + r"\b", TESTS), f"no test uses .{name} ({cls.__name__})"


def test_every_server_route_is_exercised():
    src = (ROOT / "openfusion/gui/server.py").read_text() + (ROOT / "openfusion/gui/workspace.py").read_text()
    routes = set(re.findall(r'"(/api/[a-z_/]+)"', src))
    acts = re.search(r"def file_action.*?return None", src, re.S).group(0)
    routes |= {f"/api/file/{a}" for a in re.findall(r'act == "(\w+)"', acts)}
    routes.discard("/api/file/")
    missing = sorted(r for r in routes if r not in TESTS)
    assert not missing, f"untested routes: {missing}"


def test_every_command_is_exercised():
    src = (ROOT / "openfusion/cli.py").read_text()
    cmds = re.findall(r'sub\.add_parser\("(\w+)"', src)
    assert set(cmds) == {"build", "watch", "view", "gui", "app", "new"}
    for c in cmds:
        assert re.search(rf'"{c}"', TESTS), f"no test runs `openfusion {c}`"


def test_examples_build_cleanly():
    from openfusion import Engine
    for p in sorted((ROOT / "examples").glob("*.ofs")):
        r = Engine().run_file(str(p))
        assert r.ok, (p.name, r.report())


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_gui_tools_match_the_engine():
    out = subprocess.run(["node", "test/dump_tools.js"], cwd=ROOT / "gui-src", capture_output=True, text=True, check=True)
    tools = json.loads(out.stdout)
    covered = set()
    for tid, t in tools.items():
        for op in t["ops"]:
            assert op in scriptedit.OPS, f"tool {tid}: unknown op {op}"
            covered.add(op)
        params = set().union(*(scriptedit.op_params(op) for op in t["ops"])) if t["ops"] else set()
        for f in t["fields"]:
            if f["kind"] in ("info", "gui"):           # display-only fields
                continue
            assert f["key"] in params, f"tool {tid}: field {f['key']} is not a parameter of {t['ops']}"
        # every required parameter of a single-op tool has a field
        if len(t["ops"]) == 1:
            keys = {f["key"] for f in t["fields"]}
            missing = scriptedit.required_params(t["ops"][0]) - keys
            assert not missing, f"tool {tid}: no field for required {missing}"
    # every operation can be made from the toolbar
    # (join/cut/intersect cover combine; delete in the timeline covers remove)
    assert scriptedit.OPS - covered <= {"place_on_bed", "set_anchor", "set_role", "remove", "combine"}, scriptedit.OPS - covered
