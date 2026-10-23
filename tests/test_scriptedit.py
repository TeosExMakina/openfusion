"""GUI <-> script round trip: edits change only what they must."""
import pytest

from openfusion import Engine
from openfusion.scriptedit import (EditError, delete_feature, delete_param, dependents, describe,
                                   insert_feature, move_feature, rename_feature, set_args, set_param)

SRC = '''# My bracket. Comments and spacing must survive every GUI edit.
from openfusion import *

params(
    w = 40 * mm,      # width
    t = 4 * mm,
)

plate = box(length=60, width=P.w, height=P.t)   # the base
post = cylinder(r=5, h=20)
place(post.anchor("-Z"), onto=plate.anchor("+Z"),
      gap=0)    # keep me
join(plate, tools=[post])
fil = fillet(plate, edges=[edge_between(post.face("side"), plate.face("+Z"))], r=1)
'''


def builds(text):
    r = Engine().run_source(text)
    assert r.script_error is None, r.script_error
    assert all(x.status == "ok" for x in r.results), r.report()
    return r


def test_describe_and_auto_names():
    d = describe(SRC)
    assert d["gui"] is True
    assert [f["name"] for f in d["features"]] == ["plate", "post", "place1", "join1", "fil"]
    assert d["features"][2]["args"]["source"] == 'post.anchor("-Z")'
    assert d["features"][0]["args"]["width"] == "P.w"
    assert d["params"] == {"w": "40 * mm", "t": "4 * mm"}


def test_insert_appends_named_statement():
    out, name = insert_feature(SRC, "sphere", {"r": "3", "at": 'plate.anchor("+X")'})
    assert name == "body1"
    assert out.startswith(SRC)
    assert out.endswith('body1 = sphere(r=3, at=plate.anchor("+X"))\n')
    builds(out)


def test_insert_after_marker():
    out, name = insert_feature(SRC, "box", {"length": "1", "width": "1", "height": "1"}, after="post")
    lines = out.splitlines()
    i = lines.index("post = cylinder(r=5, h=20)")
    assert lines[i + 1] == "body1 = box(length=1, width=1, height=1)"
    builds(out)


def test_set_args_splices_only_the_value():
    out = set_args(SRC, "place1", {"gap": "2.5"})
    assert out == SRC.replace("gap=0)", "gap=2.5)")
    out = set_args(SRC, "plate", {"length": "P.w * 2", "origin": '"center_xy"'})
    assert 'plate = box(length=P.w * 2, width=P.w, height=P.t, origin="center_xy")   # the base' in out
    out2 = set_args(out, "plate", {"origin": None})
    assert out2 == SRC.replace("length=60", "length=P.w * 2")
    builds(out)


def test_set_args_positional_and_unknown():
    out = set_args(SRC, "join1", {"target": "plate", "keep_tools": '"reference"'})
    assert 'join(plate, tools=[post], keep_tools="reference")' in out
    with pytest.raises(EditError):
        set_args(SRC, "join1", {"nope": "1"})
    with pytest.raises(EditError):
        set_args(SRC, "post", {"r": None})


def test_dependents_and_delete():
    assert dependents(SRC, "post") == ["place1", "join1", "fil"]
    with pytest.raises(EditError, match="used by"):
        delete_feature(SRC, "post")
    out = delete_feature(SRC, "fil")
    assert out == SRC.replace('fil = fillet(plate, edges=[edge_between(post.face("side"), plate.face("+Z"))], r=1)\n', "")
    out = delete_feature(SRC, "post", cascade=True)
    assert "post" not in out.split("params(")[1] and "# My bracket" in out
    builds(out)


def test_rename_updates_uses_and_face_strings():
    src = SRC + 'g = sphere(r=1, at=plate.face("post.side").anchor())\n'
    out = rename_feature(src, "post", "pillar")
    assert "pillar = cylinder" in out and 'pillar.anchor("-Z")' in out and '"pillar.side"' in out
    assert 'pillar.face("side")' in out
    builds(out)
    out = rename_feature(SRC, "place1", "seat")   # unassigned statement gets a name
    assert 'seat = place(post.anchor("-Z")' in out
    with pytest.raises(EditError):
        rename_feature(SRC, "post", "plate")
    with pytest.raises(EditError):
        rename_feature(SRC, "post", "2bad")


def test_move_feature_respects_order():
    out = move_feature(SRC, "post", None)
    assert out.index("post = cylinder") < out.index("plate = box")
    builds(out)
    with pytest.raises(EditError, match="before it is created"):
        move_feature(SRC, "join1", "plate")


def test_params_edit_add_delete():
    out = set_param(SRC, "w", "45 * mm")
    assert out == SRC.replace("w = 40 * mm,", "w = 45 * mm,")
    out = set_param(SRC, "gap", "0.2 * mm")
    assert "    t = 4 * mm,\n    gap = 0.2 * mm,\n)" in out
    builds(out)
    with pytest.raises(EditError, match="still used"):
        delete_param(SRC, "w")
    out = delete_param(set_param(SRC, "gap", "1"), "gap")
    assert out == SRC
    bare = "from openfusion import *\nb = box(length=1, width=1, height=1)\n"
    out = set_param(bare, "k", "2")
    assert 'params({\n    "k": 2,\n})' in out          # new blocks are a dict
    builds(out)


def test_code_only_mode():
    src = SRC + "for i in range(3):\n    sphere(r=1, at=world.point(i * 5, 0, 30))\n"
    d = describe(src)
    assert d["gui"] is False and d["blockers"] == [15]
    out, _ = insert_feature(src, "box", {"length": "1", "width": "1", "height": "1"})
    builds(out)
    nested = SRC + "cut(plate, tools=[cylinder(r=1, h=4)])\n"
    assert describe(nested)["gui"] is False


def test_unicode_offsets():
    src = "from openfusion import *\n# Ω größe\nb = box(length=1, width=1, height=1)  # é\n"
    out = set_args(src, "b", {"height": "2"})
    assert out == src.replace("height=1", "height=2")


def test_new_bodies_are_named_body_n_and_other_steps_after_their_operation():
    from openfusion.scriptedit import default_base, insert_feature
    assert default_base("box") == default_base("mirror") == default_base("split") == "body"
    assert default_base("loft", {}) == default_base("loft", {"op": '"new"'}) == "body"
    assert default_base("loft", {"op": '"join"'}) == "loft" and default_base("fillet") == "fillet"
    assert default_base("profile") == "profile" and default_base("pattern_rect") == "pattern_rect"
    text, n1 = insert_feature("from openfusion import *\n", "box", {"length": "1", "width": "1", "height": "1"})
    text, n2 = insert_feature(text, "cylinder", {"r": "1", "h": "1"})
    text, n3 = insert_feature(text, "fillet", {"edges": "body1.edges()", "r": "0.1"})
    assert (n1, n2, n3) == ("body1", "body2", "fillet1")


REUSED = '''from openfusion import *
plate = box(length=40, width=30, height=10)
lug = extrude(plate.face("+Z"), 5)
cut1 = extrude(plate.face("lug.end"), -2, op="cut")
post = cylinder(r=3, h=8, at=world.point(60, 0, 0))
lug = extrude(post.face("+Z"), 2)
'''


def test_a_reused_variable_names_steps_the_way_the_engine_does():
    """Python keeps only a variable's last value: the earlier step gets an automatic name, in
    the app exactly as in the engine (they used to disagree, which broke the timeline)."""
    d = describe(REUSED)
    names = [f["name"] for f in d["features"]]
    assert names == ["plate", "extrude1", "cut1", "post", "lug"]
    r = Engine().run_source(REUSED)
    assert [x.name for x in r.results] == names
    assert d["shadowed"] == [{"name": names[1], "variable": "lug", "line": 3, "again": 6}]
    assert d["features"][1]["variable"] == "lug" and not d["features"][1]["assigned"]
    # deleting or editing the earlier step touches that statement, not the later one
    first = names[1]
    assert "extrude(plate.face(\"+Z\"), 5)" not in delete_feature(REUSED, first, cascade=True)
    assert "post.face" in delete_feature(REUSED, first, cascade=True)


def test_fix_names_gives_each_step_its_own_name_and_keeps_what_was_meant():
    from openfusion.scriptedit import fix_names, shadowed_steps
    fixed = fix_names(REUSED)
    assert not shadowed_steps(fixed)
    first = describe(REUSED)["features"][1]["name"]
    assert f'{first} = extrude(plate.face("+Z"), 5)' in fixed
    # "lug.end" was written while lug meant the first extrude: it follows it
    assert f'plate.face("{first}.end")' in fixed and 'lug = extrude(post.face("+Z"), 2)' in fixed
    r = Engine().run_source(fixed)
    assert all(x.status == "ok" for x in r.results), r.report()
    assert fix_names(fixed) == fixed


def test_dependents_include_face_names_in_strings():
    src = ('from openfusion import *\nplate = box(length=40, width=30, height=10)\n'
           'lug = extrude(plate.face("+Z"), 5)\nf = fillet(plate, edges=[plate.edge("lug.end|lug.side(+X+Z)")], r=1)\n')
    assert dependents(src, "lug") == ["f"]
