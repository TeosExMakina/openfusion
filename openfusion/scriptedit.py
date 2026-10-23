"""Surgical edits to .ofs scripts: the GUI <-> script round trip.

The script file is the single source of truth. The GUI never regenerates it:
every action splices exactly the characters it changes (one argument, one
statement, one parameter), so comments, blank lines and formatting elsewhere
survive byte for byte.

A script is in the *GUI subset* when every top-level statement is one of
  * an import or a docstring
  * a params(...) call: params({"wall": 2 * mm, ...}) (a dict, one entry per line with an
    optional # comment) or params(wall=2 * mm, ...)
  * a feature: `name = op(...)` or `op(...)`, with exactly one OpenFusion
    operation call in it
Anything else (loops, functions, conditionals) puts the file in code-only
mode: it still builds and displays, new features can still be appended, but
existing features are edited as code.
"""
from __future__ import annotations

import ast
import keyword
import inspect
import re
from dataclasses import dataclass, field

from . import ops as _ops

OPS = {
    "box", "cylinder", "cone", "sphere", "torus", "coil", "polygon",
    "place", "place_on_bed", "move", "rotate", "copy", "pattern_rect", "pattern_circ", "mirror", "scale",
    "combine", "join", "cut", "intersect", "fillet", "chamfer", "extrude", "split",
    "set_anchor", "set_role", "remove",
    "profile", "revolve", "loft", "sweep", "shell", "draft", "sketch", "hole", "thread", "text", "plane", "axis", "import_file", "gear",
}
CREATES = {"box", "cylinder", "cone", "sphere", "torus", "coil", "polygon", "copy", "mirror",
           "pattern_rect", "pattern_circ", "split", "profile", "revolve", "loft", "sweep", "sketch",
           "text", "plane", "axis", "import_file", "gear"}


class EditError(ValueError):
    pass


def op_params(op: str) -> list[str]:
    """Parameter names of an operation, in positional order (without ctx)."""
    fn = getattr(_ops, op, None)
    if fn is None:
        raise EditError(f"unknown operation '{op}'")
    impl = getattr(fn, "__wrapped__", fn)
    names = list(inspect.signature(impl).parameters)
    return names[1:] if names and names[0] == "ctx" else names


def required_params(op: str) -> set[str]:
    fn = getattr(_ops, op)
    impl = getattr(fn, "__wrapped__", fn)
    return {n for n, p in inspect.signature(impl).parameters.items()
            if n != "ctx" and p.default is inspect.Parameter.empty}


@dataclass
class Arg:
    key: str          # canonical parameter name
    text: str         # source text of the value
    start: int        # char span of the value
    end: int
    span_start: int   # span of the whole argument (incl. "key=")
    keyword: bool


@dataclass
class Stmt:
    kind: str                     # import | doc | params | feature | other
    start: int                    # char span of the whole statement (line-aligned start)
    end: int
    line: int
    end_line: int
    name: str | None = None       # assigned variable
    op: str | None = None
    args: list = field(default_factory=list)
    call_end: int = 0             # offset of the closing parenthesis
    refs: set = field(default_factory=set)   # variable names it reads
    style: str = "kwargs"         # params only: "dict" for params({...}), else "kwargs"
    call_start: int = 0
    strs: tuple = ()              # string constants in the call (face names like "lug.end")
    shadowed: bool = False        # its variable is assigned again later, so the step has no name of its own
    target_span: tuple = (0, 0)   # where the variable is written

    def arg(self, key) -> Arg | None:
        return next((a for a in self.args if a.key == key), None)


class Script:
    def __init__(self, text: str):
        self.text = text
        self.tree = None
        self.error = None
        self.stmts: list[Stmt] = []
        self._line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._line_starts.append(i + 1)
        try:
            self.tree = ast.parse(text)
        except SyntaxError as e:
            self.error = f"SyntaxError: {e.msg} (line {e.lineno})"
            return
        for node in self.tree.body:
            self.stmts.append(self._classify(node))
        # Python keeps only the last value of a variable, and the engine names steps after the
        # variables that hold them at the end: a step whose variable is assigned again later
        # gets an automatic name (op1, op2...) like a step with no variable.
        last = {}
        for i, st in enumerate(self.stmts):
            if st.name:
                last[st.name] = i
        for i, st in enumerate(self.stmts):
            st.shadowed = bool(st.name) and last[st.name] != i

    # ------------------------------------------------------------ positions
    def off(self, lineno: int, col_bytes: int) -> int:
        start = self._line_starts[lineno - 1]
        line_end = self._line_starts[lineno] if lineno < len(self._line_starts) else len(self.text)
        line = self.text[start:line_end]
        return start + len(line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore"))

    def span(self, node) -> tuple[int, int]:
        return self.off(node.lineno, node.col_offset), self.off(node.end_lineno, node.end_col_offset)

    # ------------------------------------------------------------ classification
    def _classify(self, node) -> Stmt:
        a, b = self.span(node)
        st = Stmt("other", self._line_starts[node.lineno - 1], b, node.lineno, node.end_lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            st.kind = "import"
            return st
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            st.kind = "doc"
            return st
        call, target = None, None
        if isinstance(node, ast.Assign):
            for t in node.targets:          # any assignment counts as a variable (for shadowing)
                if isinstance(t, ast.Name):
                    st.name, st.target_span = t.id, self.span(t)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
              and isinstance(node.value, ast.Call)):
            call, target = node.value, node.targets[0].id
        if call is None or not isinstance(call.func, ast.Name):
            return st
        fname = call.func.id
        nested = [n for n in ast.walk(call) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id in OPS and n is not call]
        if fname == "params" and not nested:
            st.kind = "params"
            st.name = target
            st.call_start = self.span(call)[0]
            d = call.args[0] if len(call.args) == 1 and not call.keywords else None
            if isinstance(d, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in d.keys):
                st.style = "dict"
                for k, v in zip(d.keys, d.values):
                    ks, _ = self.span(k)
                    vs, ve = self.span(v)
                    st.args.append(Arg(k.value, self.text[vs:ve], vs, ve, ks, True))
                st.call_end = self.span(d)[1] - 1
            else:
                st.args = self._args(call, None)
                st.call_end = self.span(call)[1] - 1
            return st
        if fname not in OPS or nested:
            return st
        st.kind, st.name, st.op = "feature", target, fname
        st.args = self._args(call, op_params(fname))
        st.call_end = self.span(call)[1] - 1
        st.refs = {n.id for n in ast.walk(call) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        st.strs = tuple(n.value for n in ast.walk(call) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        return st

    def _args(self, call, names):
        out = []
        for i, a in enumerate(call.args):
            if isinstance(a, ast.Starred):
                raise EditError("*args are not supported in feature statements")
            s, e = self.span(a)
            key = names[i] if names and i < len(names) else str(i)
            out.append(Arg(key, self.text[s:e], s, e, s, False))
        for kw in call.keywords:
            if kw.arg is None:
                continue
            s, e = self.span(kw.value)
            ks = self.text.rfind(kw.arg, 0, s)
            out.append(Arg(kw.arg, self.text[s:e], s, e, ks, True))
        return out

    # ------------------------------------------------------------ queries
    @property
    def gui_ok(self) -> bool:
        return self.error is None and all(s.kind != "other" for s in self.stmts)

    def blockers(self) -> list[int]:
        return [s.line for s in self.stmts if s.kind == "other"]

    def features(self) -> list[Stmt]:
        return [s for s in self.stmts if s.kind == "feature"]

    def params_stmt(self) -> Stmt | None:
        return next((s for s in self.stmts if s.kind == "params"), None)

    def variables(self) -> set[str]:
        return {s.name for s in self.stmts if s.name}


# ===================================================================== naming
def auto_names(script: Script) -> list[str]:
    """Names the engine gives each feature statement (variables, or op1, op2...)."""
    taken = set(script.variables())
    counters: dict[str, int] = {}
    out = []
    for s in script.features():
        if s.name and not s.shadowed:
            out.append(s.name)
            continue
        n = counters.get(s.op, 0)
        while True:
            n += 1
            if f"{s.op}{n}" not in taken:
                break
        counters[s.op] = n
        taken.add(f"{s.op}{n}")
        out.append(f"{s.op}{n}")
    return out


# Steps that make a new solid body get body1, body2... (a box rarely stays a box for long);
# everything else is named after its operation (fillet1, pattern1, profile1...).
NEW_BODY = {"box", "cylinder", "cone", "sphere", "torus", "coil", "polygon", "copy", "mirror", "split", "import_file", "gear"}


def default_base(op: str, args: dict | None = None) -> str:
    if op in NEW_BODY:
        return "body"
    if op in ("revolve", "loft", "sweep"):
        mode = str((args or {}).get("op", '"new"')).strip().strip("'\"")
        return "body" if mode == "new" else op
    return op


def new_name(script: Script, op: str) -> str:
    taken = set(script.variables()) | set(auto_names(script)) | set(_reserved())
    n = 1
    while f"{op}{n}" in taken:
        n += 1
    return f"{op}{n}"


def _reserved():
    import builtins
    return set(dir(builtins)) | {"P", "mm", "cm", "m", "inch", "deg", "rad", "world", "edge_between",
                                  "params"} | OPS


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def check_name(script: Script, name: str, allow: str | None = None):
    if not IDENT.match(name or ""):
        raise EditError(f"'{name}' is not a valid name: use letters, digits and _, not starting with a digit")
    if name in _reserved():
        raise EditError(f"'{name}' is reserved")
    used = (set(script.variables()) | set(auto_names(script))) - {allow}
    if name in used:
        raise EditError(f"'{name}' is already used")


# ===================================================================== edits
def _splice(text, start, end, new):
    return text[:start] + new + text[end:]


def _line_end(text, pos):
    i = text.find("\n", pos)
    return len(text) if i < 0 else i + 1


def render_call(op: str, args: dict) -> str:
    """op(k=v, ...) in the operation's parameter order. Values are source text."""
    order = op_params(op)
    keys = [k for k in order if k in args] + [k for k in args if k not in order]
    return f"{op}(" + ", ".join(f"{k}={args[k]}" for k in keys) + ")"


def insert_feature(text: str, op: str, args: dict, name: str | None = None, after: str | None = None) -> tuple[str, str]:
    """Insert `name = op(...)` after the feature `after` (default: end of file). Returns (text, name)."""
    sc = Script(text)
    if sc.error:
        raise EditError(sc.error)
    name = name or new_name(sc, default_base(op, args))
    check_name(sc, name)
    for k in args:
        if k not in op_params(op):
            raise EditError(f"{op} has no parameter '{k}'")
    line = f"{name} = {render_call(op, args)}\n"
    if after:
        names = auto_names(sc)
        feats = sc.features()
        if after not in names:
            raise EditError(f"no feature '{after}'")
        st = feats[names.index(after)]
        pos = _line_end(text, st.end)
        return text[:pos] + line + text[pos:], name
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line, name


def _feature_stmt(sc: Script, name: str) -> Stmt:
    names = auto_names(sc)
    if name not in names:
        raise EditError(f"no feature '{name}'")
    return sc.features()[names.index(name)]


def set_args(text: str, name: str, changes: dict) -> str:
    """Change arguments of a feature statement. changes: key -> new source text, or None to remove."""
    sc = Script(text)
    if sc.error:
        raise EditError(sc.error)
    st = _feature_stmt(sc, name)
    valid = op_params(st.op)
    for k in changes:
        if k not in valid:
            raise EditError(f"{st.op} has no parameter '{k}'")
    edits = []  # (start, end, new)
    for k, v in changes.items():
        a = st.arg(k)
        if a is not None and v is not None:
            if a.text != v:
                edits.append((a.start, a.end, v))
        elif a is not None and v is None:
            if not a.keyword or k in required_params(st.op):
                raise EditError(f"'{k}' is a required argument and cannot be removed")
            # remove ", key=value" (or "key=value, " if first)
            s, e = a.span_start, a.end
            before = text[:s].rstrip()
            if before.endswith(","):
                s = len(before) - 1
            else:
                m = re.match(r"\s*,\s*", text[e:])
                if m:
                    e += m.end()
            edits.append((s, e, ""))
        elif a is None and v is not None:
            pos = st.call_end
            if st.args:
                last = max(st.args, key=lambda x: x.end)
                tail = text[last.end:st.call_end]
                if tail.strip().startswith(","):
                    pos = last.end + tail.index(",") + 1
                    edits.append((pos, pos, f" {k}={v},"))
                else:
                    edits.append((last.end, last.end, f", {k}={v}"))
            else:
                edits.append((pos, pos, f"{k}={v}"))
    for s, e, new in sorted(edits, key=lambda x: x[0], reverse=True):
        text = _splice(text, s, e, new)
    return text


def _name_pattern(name):
    """A feature's name used in a face or edge name string: "lug.end", "lug_2.+Z", "f1(+X+Z)"."""
    return re.compile(r"(?<![\w.])" + re.escape(name) + r"(?=(_\d+)?[.(|#])")


def dependents(text: str, name: str) -> list[str]:
    """Features after `name` that use it (transitively): they read the variable that holds it
    (until the variable is assigned again), or name its faces in a string ("lug.end")."""
    sc = Script(text)
    names = auto_names(sc)
    feats = sc.features()
    if name not in names:
        raise EditError(f"no feature '{name}'")
    i = names.index(name)
    gone_vars = {feats[i].name} if feats[i].name else set()
    gone_names = [name]
    out = []
    for n, st in zip(names[i + 1:], feats[i + 1:]):
        hit = bool(st.refs & gone_vars) or any(_name_pattern(g).search(x) for g in gone_names for x in st.strs)
        if hit:
            out.append(n)
            gone_names.append(n)
            if st.name:
                gone_vars.add(st.name)
        elif st.name:
            gone_vars.discard(st.name)          # the variable now holds something else
    return out


def _detach_edits(sc: "Script", st: "Stmt", gone_vars: set, gone_names: list):
    """Edits that take every use of the gone steps out of statement `st`, when each use is one
    item of a list that keeps others (cut(plate, [a, b]) -> cut(plate, [b])). None when a use
    can't be taken out (the body being changed, a position, a face named in a string)."""
    if any(_name_pattern(g).search(x) for g in gone_names for x in st.strs):
        return None
    node = sc.tree.body[sc.stmts.index(st)]
    parent = {}
    for n in ast.walk(node):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    drop = {}                                    # list node -> elements to drop
    for n in ast.walk(node):
        if not (isinstance(n, ast.Name) and n.id in gone_vars and isinstance(n.ctx, ast.Load)):
            continue
        e = n                                    # the whole expression built on it: a[2], a.face("x").anchor()
        while e in parent and (isinstance(parent[e], (ast.Subscript, ast.Attribute)) and parent[e].value is e
                               or isinstance(parent[e], ast.Call) and parent[e].func is e):
            e = parent[e]
        lst = parent.get(e)
        if not isinstance(lst, (ast.List, ast.Tuple)) or e not in lst.elts:
            return None
        drop.setdefault(lst, set()).add(e)
    edits = []
    for lst, gone in drop.items():
        keep = [x for x in lst.elts if x not in gone]
        if not keep:
            return None
        a, b = sc.span(lst)
        src = ", ".join(sc.text[slice(*sc.span(x))] for x in keep)
        if isinstance(lst, ast.Tuple):
            src = f"({src},)" if len(keep) == 1 else f"({src})"
        else:
            src = f"[{src}]"
        edits.append((a, b, src))
    return edits


def delete_plan(text: str, name: str) -> dict:
    """What deleting `name` does to the steps that use it, keeping as many as possible:
    'keeps': steps that only had it in a list (taken out of the list), 'also': steps that
    can't do without it (deleted too, with whatever needs them)."""
    sc = Script(text)
    names = auto_names(sc)
    feats = sc.features()
    if name not in names:
        raise EditError(f"no feature '{name}'")
    i = names.index(name)
    gone_vars = {feats[i].name} if feats[i].name else set()
    gone_names = [name]
    keeps, also, edits = [], [], []
    for n, st in zip(names[i + 1:], feats[i + 1:]):
        hit = bool(st.refs & gone_vars) or any(_name_pattern(g).search(x) for g in gone_names for x in st.strs)
        if hit:
            ed = _detach_edits(sc, st, gone_vars, gone_names)
            if ed is None:
                also.append(n)
                gone_names.append(n)
                if st.name:
                    gone_vars.add(st.name)
                continue
            keeps.append(n)
            edits += ed
        if st.name:
            gone_vars.discard(st.name)           # the variable now holds something else
    return {"keeps": keeps, "also": also, "edits": edits}


def delete_feature(text: str, name: str, cascade: bool = False, detach: bool = False) -> str:
    """Delete a step. With steps that use it: cascade deletes them too; detach takes it out of
    the lists that name it (a cut's tools...) and deletes only the steps that can't do without it."""
    sc = Script(text)
    deps = dependents(text, name)
    names = auto_names(sc)
    feats = sc.features()
    edits = []
    if detach:
        plan = delete_plan(text, name)
        victims = [name] + plan["also"]
        edits = plan["edits"]
    elif deps and not cascade:
        raise EditError(f"'{name}' is used by {', '.join(deps)}")
    else:
        victims = [name] + (deps if cascade else [])
    for v in victims:
        st = feats[names.index(v)]
        edits.append((st.start, _line_end(text, st.end), ""))
    for a, b, new in sorted(edits, reverse=True):
        text = text[:a] + new + text[b:]
    return text


def rename_feature(text: str, old: str, new: str, intent: bool = False) -> str:
    """Rename a feature everywhere: its variable, every use of it, and face names in strings
    ("old.side", "old_2.+Z", "old(+X+Z)"). Uses of its variable are renamed only while the
    variable still holds this feature (up to where it is assigned again).
    intent: for a step whose variable is assigned again later (see fix_names), strings written
    with that variable's name before then ("extrude1.end") are taken to mean this step too."""
    sc = Script(text)
    check_name(sc, new, allow=old)
    st = _feature_stmt(sc, old)
    edits = []
    stmts = sc.stmts
    i = stmts.index(st)
    var = st.name
    if var is None:
        edits.append((st.start + (len(text[st.start:]) - len(text[st.start:].lstrip())), None, f"{new} = "))
        scope = []
    else:
        edits.append((st.target_span[0], st.target_span[1], new))
        scope = []
        for later in stmts[i + 1:]:
            scope.append(later)
            if later.name == var:
                break
    pats = [_name_pattern(old)]
    pats_scoped = [_name_pattern(var)] if intent and st.shadowed and var else []
    def in_scope(pos):
        return any(x.start <= pos < _line_end(text, x.end) for x in scope)

    for node in ast.walk(sc.tree):
        if isinstance(node, ast.Name) and var is not None and node.id == var and isinstance(node.ctx, ast.Load):
            s_, e_ = sc.span(node)
            if in_scope(s_):
                edits.append((s_, e_, new))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s_, e_ = sc.span(node)
            raw = text[s_:e_]
            out = raw
            for pt in pats:
                out = pt.sub(new, out)
            if pats_scoped and in_scope(s_):
                for pt in pats_scoped:
                    out = pt.sub(new, out)
            if out != raw:
                edits.append((s_, e_, out))
    for s_, e_, v in sorted(edits, key=lambda x: x[0], reverse=True):
        text = text[:s_] + v + text[s_ if e_ is None else e_:]
    return text


def shadowed_steps(text: str) -> list[dict]:
    """Steps whose variable is assigned again later: [{name (what the step is called),
    variable, line, again (the line that reuses the variable)}]."""
    sc = Script(text)
    if sc.error:
        return []
    out = []
    for n, st in zip(auto_names(sc), sc.features()):
        if st.shadowed:
            again = next(x.line for x in sc.stmts[sc.stmts.index(st) + 1:] if x.name == st.name)
            out.append({"name": n, "variable": st.name, "line": st.line, "again": again})
    return out


def fix_names(text: str) -> str:
    """Give each step whose variable is reused later a name of its own (the automatic name the
    app already shows), so every step has one name, as written. Face names written as
    "variable.face" between the step and the reuse are taken to mean that step."""
    for _ in range(100):
        sh = shadowed_steps(text)
        if not sh:
            return text
        text = rename_feature(text, sh[0]["name"], sh[0]["name"], intent=True)
    raise EditError("could not untangle the reused names")


def move_feature(text: str, name: str, after: str | None) -> str:
    """Move a feature statement to just after `after` (None = top of the features)."""
    sc = Script(text)
    st = _feature_stmt(sc, name)
    block = text[st.start:_line_end(text, st.end)]
    rest = text[:st.start] + text[_line_end(text, st.end):]
    sc2 = Script(rest)
    names2 = auto_names(sc2)
    if after is None:
        first = sc2.features()[0] if sc2.features() else None
        pos = first.start if first else len(rest)
    else:
        a = sc2.features()[names2.index(after)]
        pos = _line_end(rest, a.end)
    out = rest[:pos] + block + rest[pos:]
    # refuse moves that put a use before its definition
    sc3 = Script(out)
    seen = set()
    for n, s in zip(auto_names(sc3), sc3.features()):
        later = {m for m in auto_names(sc3)} - seen - {n}
        if s.refs & (later & sc3.variables()):
            bad = sorted(s.refs & later & sc3.variables())[0]
            raise EditError(f"'{n}' would use '{bad}' before it is created")
        seen.add(n)
    return out


# ===================================================================== parameters
# Parameters live in one params(...) statement. The app reads and edits them as entries
# (name, expression, comment); edits to a dict-style block, renames and comments rewrite the
# block in a tidy, aligned form:
#
#     params({
#         "wall":   2 * mm,     # wall thickness
#         "length": 60 * mm,
#     })
#
# Simple value changes in a keyword-style params(a=..., b=...) block are spliced in place.

def param_values(text: str) -> dict:
    sc = Script(text)
    st = sc.params_stmt()
    return {a.key: a.text for a in st.args} if st else {}


def param_entries(text: str) -> list[dict]:
    """[{name, expr, comment, lead, line}] in script order. comment: the '# ...' after the
    entry on its line; lead: comment lines directly above it inside the block."""
    sc = Script(text)
    st = sc.params_stmt()
    if st is None:
        return []
    out, prev_end = [], text.find("(", st.call_start) + 1
    if st.style == "dict":
        prev_end = text.find("{", st.call_start) + 1
    for a in sorted(st.args, key=lambda x: x.span_start):
        lead = []
        between = text[prev_end:a.span_start]
        for ln in between.split("\n") if "\n" in between or prev_end == text.rfind("\n", 0, a.span_start) + 1 else []:
            t = ln.strip()
            if t.startswith("#"):
                lead.append(t[1:].strip())
        rest = text[a.end:_line_end(text, a.end)]
        rest_s = rest.lstrip()
        if rest_s.startswith(","):
            rest_s = rest_s[1:].lstrip()
        comment = rest_s[1:].strip() if rest_s.startswith("#") else ""
        line = text.count("\n", 0, a.span_start) + 1
        out.append({"name": a.key, "expr": a.text, "comment": comment, "lead": lead, "line": line})
        prev_end = _line_end(text, a.end) if comment else a.end
    return out


def render_params(entries: list[dict], prefix: str = "") -> str:
    if not entries:
        return f"{prefix}params({{}})"
    keyw = max(len(e["name"]) for e in entries) + 3          # "name":
    body = [f'    {(chr(34) + e["name"] + chr(34) + ":").ljust(keyw)} {e["expr"]},' for e in entries]
    valw = max(len(b) for b in body)
    lines = []
    for e, b in zip(entries, body):
        for c in e.get("lead") or []:
            lines.append(f"    # {c}" if c else "    #")
        c = (e.get("comment") or "").strip()
        lines.append(f"{b.ljust(valw)}  # {c}" if c else b)
    return prefix + "params({\n" + "\n".join(lines) + "\n})"


def write_params(text: str, entries: list[dict]) -> str:
    """Replace the params statement with `entries` (or add one after the imports)."""
    sc = Script(text)
    if sc.error:
        raise EditError(sc.error)
    st = sc.params_stmt()
    if st is None:
        imp = [x for x in sc.stmts if x.kind in ("import", "doc")]
        pos = _line_end(text, imp[-1].end) if imp else 0
        after = text[pos:]
        return text[:pos] + "\n" + render_params(entries) + "\n" + ("" if after.startswith("\n") or not after else "\n") + after
    prefix = text[st.start:st.call_start]
    # keep a comment that trails the closing parenthesis
    return text[:st.start] + render_params(entries, prefix) + text[st.end:]


def _check_value(value_text: str):
    try:
        ast.parse(value_text, mode="eval")
    except SyntaxError:
        raise EditError(f"'{value_text}' is not a valid value")
    if "\n" in value_text:
        raise EditError("a parameter value must be on one line")


def _check_param_name(text: str, name: str, existing):
    # read as P.name, so only Python keywords are off limits (P.box is fine, P.class is not)
    if not IDENT.match(name or "") or keyword.iskeyword(name):
        raise EditError(f"'{name}' is not a valid parameter name: use letters, digits and _, not starting with a digit")
    if name in existing:
        raise EditError(f"there is already a parameter '{name}'")


def set_param(text: str, name: str, value_text: str) -> str:
    sc = Script(text)
    if sc.error:
        raise EditError(sc.error)
    _check_value(value_text)
    st = sc.params_stmt()
    if st is None or st.style == "dict":
        entries = param_entries(text)
        hit = next((e for e in entries if e["name"] == name), None)
        if hit is not None:
            hit["expr"] = value_text.strip()
        else:
            _check_param_name(text, name, {e["name"] for e in entries})
            entries.append({"name": name, "expr": value_text.strip(), "comment": "", "lead": []})
        return write_params(text, entries)
    a = st.arg(name)
    if a is not None:
        return _splice(text, a.start, a.end, value_text)
    _check_param_name(text, name, set())
    if st.args:
        last = max(st.args, key=lambda x: x.end)
        tail = text[last.end:st.call_end]
        multiline = "\n" in text[st.args[0].span_start - 10 if st.args[0].span_start >= 10 else 0:st.call_end]
        if multiline:
            line_start = text.rfind("\n", 0, last.span_start) + 1
            indent = text[line_start:last.span_start]
            indent = indent if indent.strip() == "" else "    "
            if tail.strip().startswith(","):
                pos = last.end + tail.index(",") + 1
                return _splice(text, pos, pos, f"\n{indent}{name} = {value_text},")
            return _splice(text, last.end, last.end, f",\n{indent}{name} = {value_text}")
        return _splice(text, last.end, last.end, f", {name}={value_text}")
    return _splice(text, st.call_end, st.call_end, f"{name}={value_text}")


def param_users(text: str, name: str) -> list[str]:
    """Feature names whose arguments use P.name."""
    sc = Script(text)
    if sc.error:
        return []
    pat = re.compile(r"\bP\." + re.escape(name) + r"\b")
    return [n for n, st in zip(auto_names(sc), sc.features()) if any(pat.search(a.text) for a in st.args)]


def delete_param(text: str, name: str) -> str:
    sc = Script(text)
    used = param_users(text, name)
    if used or re.search(r"\bP\." + re.escape(name) + r"\b", text):
        raise EditError(f"parameter '{name}' is still used" + (f" by {', '.join(used)}" if used else ""))
    st = sc.params_stmt()
    a = st.arg(name) if st else None
    if a is None:
        raise EditError(f"no parameter '{name}'")
    if st.style == "dict":
        return write_params(text, [e for e in param_entries(text) if e["name"] != name])
    s, e = a.span_start, a.end
    m = re.match(r"[ \t]*,", text[e:])
    if m:
        e += m.end()
    ls = text.rfind("\n", 0, s) + 1
    if text[ls:s].strip() == "" and text[e:_line_end(text, e)].strip() == "":
        s, e = ls, _line_end(text, e)
    return text[:s] + text[e:]


def rename_param(text: str, old: str, new: str) -> str:
    """Rename a parameter and every P.old in the script."""
    entries = param_entries(text)
    hit = next((e for e in entries if e["name"] == old), None)
    if hit is None:
        raise EditError(f"no parameter '{old}'")
    if new == old:
        return text
    _check_param_name(text, new, {e["name"] for e in entries})
    text = re.sub(r"\bP\." + re.escape(old) + r"\b", "P." + new, text)
    entries = param_entries(text)
    next(e for e in entries if e["name"] == old)["name"] = new
    return write_params(text, entries)


def set_param_comment(text: str, name: str, comment: str) -> str:
    entries = param_entries(text)
    hit = next((e for e in entries if e["name"] == name), None)
    if hit is None:
        raise EditError(f"no parameter '{name}'")
    hit["comment"] = " ".join(str(comment).replace("#", " ").split())
    return write_params(text, entries)


def param_info(text: str) -> list[dict]:
    return [{**e, "used_by": param_users(text, e["name"])} for e in param_entries(text)]


# ===================================================================== description for the GUI
def describe(text: str) -> dict:
    sc = Script(text)
    if sc.error:
        return {"ok": False, "error": sc.error, "gui": False, "features": [], "params": {}}
    names = auto_names(sc)
    feats = []
    for n, st in zip(names, sc.features()):
        feats.append({"name": n, "op": st.op, "line": st.line, "end_line": st.end_line,
                      "assigned": st.name is not None and not st.shadowed, "variable": st.name,
                      "args": {a.key: a.text for a in st.args}, "creates": st.op in CREATES})
    return {"ok": True, "gui": sc.gui_ok, "blockers": sc.blockers(), "features": feats,
            "shadowed": shadowed_steps(text),
            "params": param_values(text), "param_info": param_info(text)}
