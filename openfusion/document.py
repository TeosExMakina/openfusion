"""Documents, features and the evaluator.

Running a script does not build geometry. Each API call appends a Feature
to the current Document. The Engine then evaluates the feature list in
order, against a model state (body id -> Body).

Caching: every feature gets a key = hash(previous key + its signature).
Unchanged prefixes of the timeline are reused from the cache, so editing a
parameter that is only used near the end recomputes only the end.

Failures: a feature that raises is marked 'error'. The bodies it would have
created or modified are tainted, and later features that reference them are
marked 'skipped' instead of being built from wrong inputs.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field

from .body import Body, NameError_
from .geom import Frame
from .refs import (DependencyError, FrameRef, GroupHandle, Handle, RefError,
                   WorldRef, sig)

_STACK: list["Document"] = []


def current_doc() -> "Document":
    if not _STACK:
        # Allow interactive use without an explicit document.
        _STACK.append(Document())
    return _STACK[-1]


class Document:
    def __init__(self, path: str | None = None, overrides: dict | None = None):
        self.path = path
        self.features: list[Feature] = []
        self.params: "OrderedDict[str, float]" = OrderedDict()
        self.overrides = dict(overrides or {})
        self.script_error: str | None = None
        self.script_error_line: int | None = None

    def add(self, feature: "Feature") -> "Feature":
        feature.index = len(self.features)
        self.features.append(feature)
        return feature

    def __enter__(self):
        _STACK.append(self)
        return self

    def __exit__(self, *exc):
        _STACK.remove(self)

    def assign_names(self, namespace: dict):
        """Variable names become feature names; the rest get op1, op2..."""
        taken = set()
        for k, v in namespace.items():
            if k.startswith("_"):
                continue
            feat = getattr(v, "_feature", None)
            if feat is not None and feat in self.features and feat.name is None:
                feat.name = k
                taken.add(k)
        counters: dict[str, int] = {}
        for f in self.features:
            if f.name is None:
                n = counters.get(f.op, 0)
                while True:
                    n += 1
                    cand = f"{f.op}{n}"
                    if cand not in taken:
                        break
                counters[f.op] = n
                f.name = cand
                taken.add(cand)


class Feature:
    def __init__(self, op: str, fn, args, kwargs, kind: str, line: int | None):
        self.op, self.fn, self.args, self.kwargs = op, fn, args, kwargs
        self.kind = kind  # "create" | "modify" | "group"
        self.line = line
        self.name: str | None = None
        self.index = -1

    def signature(self) -> str:
        return f"{self.name}={self.op}({sig(list(self.args))};{sig(self.kwargs)})"

    def touches(self) -> list[str]:
        if self.kind in ("create", "group"):
            return [self.name]
        target = self.args[0] if self.args else (self.kwargs.get("body") or self.kwargs.get("target")
                                                 or self.kwargs.get("edges") or self.kwargs.get("face"))
        bid = _body_of(target)
        return [bid] if bid else []

    def __repr__(self):
        return f"<feature {self.name}: {self.op}>"


def _body_of(target, depth=0):
    """The body a modify step changes: found through a handle, a face/edge reference
    (plate.face("+Z").edges(), edge_between(...)) or the first item of a list."""
    if target is None or depth > 4:
        return None
    if isinstance(target, Handle):
        return target.body_id
    if isinstance(target, (list, tuple)):
        for t in target:
            bid = _body_of(t, depth + 1)
            if bid:
                return bid
        return None
    owner = getattr(target, "owner", None)
    if callable(owner):
        try:
            o = owner()
        except Exception:
            o = None
        if isinstance(o, Handle):
            return o.body_id
    for attr in ("handle", "face_ref", "a"):
        bid = _body_of(getattr(target, attr, None), depth + 1)
        if bid:
            return bid
    return None


# ===================================================================== context
class Ctx:
    """What an operation sees while it runs."""

    def __init__(self, state: dict, tainted: frozenset, groups: dict, feature: Feature):
        self.state = dict(state)
        self.tainted = set(tainted)
        self.groups = dict(groups)
        self.feature = feature
        self.warnings: list[str] = []
        self.info: dict = {}        # facts for the app's preview (e.g. a pattern's directions)

    @property
    def fid(self) -> str:
        return self.feature.name

    def warn(self, msg: str):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def body(self, h, allow_removed=False) -> Body:
        bid = h.body_id if isinstance(h, Handle) else str(h)
        if bid in self.tainted:
            raise DependencyError(f"'{bid}' failed upstream")
        b = self.state.get(bid)
        if b is None:
            raise DependencyError(f"no body '{bid}' at this point in the timeline")
        if b.role == "removed" and not allow_removed:
            raise RefError(f"'{bid}' was removed earlier; only its design anchors can be used")
        return b

    def bodies(self, items, allow_removed=False) -> list[Body]:
        out = []
        if isinstance(items, (Handle, GroupHandle, str)):
            items = [items]
        for it in items:
            if isinstance(it, GroupHandle):
                for bid in it.members(self):
                    out.append(self.body(bid, allow_removed))
            else:
                out.append(self.body(it, allow_removed))
        return out

    def put(self, body: Body):
        self.state[body.id] = body

    def frame(self, ref, default=None) -> Frame:
        if ref is None:
            if default is None:
                return Frame.world()
            ref = default
        if isinstance(ref, Frame):
            return ref
        if isinstance(ref, Handle):
            return self.body(ref, allow_removed=True).anchor("origin")
        if isinstance(ref, FrameRef):
            return ref.frame(self)
        if isinstance(ref, str):
            return WorldRef(ref).frame(self)
        if isinstance(ref, (tuple, list)) and len(ref) == 3:
            return Frame.make(ref)
        raise TypeError(f"cannot use {ref!r} as an anchor or frame")

    def edges(self, body: Body, items) -> list:
        """Resolve edge references (refs or plain edge names on `body`)."""
        if not isinstance(items, (list, tuple)):
            items = [items]
        out = []
        for it in items:
            if isinstance(it, str):
                es = body.edges_named(it)
                if not es:
                    raise RefError(f'{body.id}.edge("{it}") not found')
                src = body
            else:
                src, es = it.resolve(self)
            if src.id != body.id:
                raise RefError(f"{it!r} is on body '{src.id}', not '{body.id}'")
            for e in es:
                if not any(e.IsSame(x) for x in out):
                    out.append(e)
        return out


# ===================================================================== results
@dataclass
class FeatureResult:
    name: str
    op: str
    status: str          # ok | warning | error | skipped
    message: str = ""
    warnings: list = field(default_factory=list)
    ms: float = 0.0
    line: int | None = None
    cached: bool = False
    info: dict = field(default_factory=dict)

    def as_dict(self):
        return {"name": self.name, "op": self.op, "status": self.status, "message": self.message,
                "warnings": list(self.warnings), "ms": round(self.ms, 1), "line": self.line,
                "cached": self.cached}


@dataclass
class BuildResult:
    bodies: dict
    results: list
    params: dict
    script_error: str | None = None
    script_error_line: int | None = None
    ms: float = 0.0
    stale: dict = field(default_factory=dict)   # body id -> the failed step that left it out of date

    @property
    def ok(self) -> bool:
        return self.script_error is None and all(r.status in ("ok", "warning") for r in self.results)

    def body(self, name) -> Body:
        return self.bodies[name]

    def solids(self) -> list[Body]:
        return [b for b in self.bodies.values() if b.role == "solid"]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        cached = sum(1 for r in self.results if r.cached)
        parts = [f"{len(self.results)} features"] + [f"{v} {k}" for k, v in sorted(counts.items())]
        return ", ".join(parts) + f" ({cached} cached), {self.ms:.0f} ms"

    def report(self) -> str:
        lines = []
        if self.script_error:
            lines.append(f"SCRIPT ERROR (line {self.script_error_line}): {self.script_error}")
        mark = {"ok": " ", "warning": "!", "error": "X", "skipped": "-"}
        for r in self.results:
            ln = f"line {r.line}" if r.line else ""
            lines.append(f" {mark[r.status]} {r.name:<14} {r.op:<12} {r.status:<8} {r.ms:7.1f} ms {'(cached)' if r.cached else '':<8} {ln}")
            if r.message:
                lines.append(f"      {r.message}")
            for w in r.warnings:
                lines.append(f"      warning: {w}")
        lines.append(self.summary())
        return "\n".join(lines)


# ===================================================================== engine
class Engine:
    def __init__(self, max_cache: int = 4000):
        self.cache: "OrderedDict[str, tuple]" = OrderedDict()
        self.max_cache = max_cache

    def evaluate(self, doc: Document, limit: int | None = None) -> BuildResult:
        t0 = time.perf_counter()
        state: dict = {}
        tainted: frozenset = frozenset()
        groups: dict = {}
        key = hashlib.sha1(b"openfusion-1").hexdigest()
        results = []
        for feat in (doc.features if limit is None else doc.features[:limit]):
            key = hashlib.sha1((key + feat.signature()).encode()).hexdigest()
            hit = self.cache.get(key)
            if hit is not None:
                self.cache.move_to_end(key)
                state, tainted, groups, res = hit
                r = FeatureResult(**{**res.__dict__, "cached": True})
                results.append(r)
                continue
            ctx = Ctx(state, tainted, groups, feat)
            t1 = time.perf_counter()
            status, message = "ok", ""
            try:
                feat.fn(ctx, *feat.args, **feat.kwargs)
                if ctx.warnings:
                    status = "warning"
            except DependencyError as e:
                status, message = "skipped", str(e)
            except (RefError, NameError_, ValueError, TypeError, RuntimeError) as e:
                status, message = "error", str(e)
            except Exception as e:  # kernel failures and bugs
                status, message = "error", f"{type(e).__name__}: {e}"
                if os.environ.get("OPENFUSION_DEBUG"):
                    traceback.print_exc()
            ms = (time.perf_counter() - t1) * 1000
            if status in ("error", "skipped"):
                new_state = dict(state)
                new_tainted = set(tainted)
                for bid in feat.touches():
                    new_tainted.add(bid)
                    new_state.pop(bid, None) if feat.kind != "modify" else None
                tainted = frozenset(new_tainted)
                state = new_state
            else:
                state, tainted, groups = ctx.state, frozenset(ctx.tainted), ctx.groups
            res = FeatureResult(feat.name, feat.op, status, message, list(ctx.warnings), ms, feat.line,
                                info=dict(ctx.info) if status in ("ok", "warning") else {})
            results.append(res)
            self.cache[key] = (state, tainted, groups, res)
            if len(self.cache) > self.max_cache:
                self.cache.popitem(last=False)
        live = {k: v for k, v in state.items()}
        stale = {}
        feats = doc.features if limit is None else doc.features[:limit]
        for feat, r in zip(feats, results):
            if r.status in ("error", "skipped"):
                for bid in feat.touches():
                    if bid in tainted and bid in live:
                        stale.setdefault(bid, feat.name)
        return BuildResult(live, results, dict(doc.params), doc.script_error, doc.script_error_line,
                           (time.perf_counter() - t0) * 1000, stale)

    def run_file(self, path: str, overrides: dict | None = None, limit: int | None = None) -> BuildResult:
        doc = load_script(path, overrides)
        return self.evaluate(doc, limit)

    def run_source(self, source: str, overrides: dict | None = None, path="<script>",
                   limit: int | None = None) -> BuildResult:
        doc = load_source(source, path, overrides)
        return self.evaluate(doc, limit)


def load_script(path: str, overrides: dict | None = None) -> Document:
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    return load_source(source, os.path.abspath(path), overrides)


def load_source(source: str, path: str, overrides: dict | None = None) -> Document:
    doc = Document(path, overrides)
    ns = {"__name__": "__openfusion__", "__file__": path}
    with doc:
        try:
            code = compile(source, path, "exec")
            exec(code, ns)
        except SyntaxError as e:
            doc.script_error, doc.script_error_line = f"SyntaxError: {e.msg}", e.lineno
        except Exception as e:
            tb = traceback.extract_tb(sys.exc_info()[2])
            line = next((fr.lineno for fr in reversed(tb) if fr.filename == path), None)
            doc.script_error, doc.script_error_line = f"{type(e).__name__}: {e}", line
    doc.assign_names(ns)
    return doc


def caller_line() -> int | None:
    """Line number in the user's script that made the current API call."""
    doc = _STACK[-1] if _STACK else None
    f = sys._getframe(2)
    while f is not None:
        if doc is not None and doc.path and f.f_code.co_filename == doc.path:
            return f.f_lineno
        f = f.f_back
    return None
