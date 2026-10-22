"""A sketch statement's arguments, read back for the app's sketch mode.

Values are kept as the script writes them: numbers become numbers, strings strings, and any
other expression (plate.anchor("+Z"), plate.edge("+X+Z"), P.width, 10 * mm) stays source text,
so writing the sketch back changes nothing the user didn't touch."""
from __future__ import annotations

import ast


def _value(node, src):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)):
        return -node.operand.value
    return {"expr": ast.get_source_segment(src, node)}


def _seq(node, src):
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_value(e, src) for e in node.elts]
    return {"expr": ast.get_source_segment(src, node)}


def sketch_args(args: dict) -> dict:
    """args: argument name -> source text (as the script describer gives them)."""
    out = {"at": args.get("at"), "spin": args.get("spin"), "points": {}, "curves": {}, "constraints": []}
    for key in ("points", "curves"):
        text = args.get(key)
        if not text:
            continue
        node = ast.parse(text, mode="eval").body
        if not isinstance(node, ast.Dict):
            raise ValueError(f"sketch {key}: the app can edit a written-out {{...}}, not {text[:40]}")
        for k, v in zip(node.keys, node.values):
            name = ast.literal_eval(k)
            out[key][name] = _seq(v, text) if isinstance(v, (ast.Tuple, ast.List)) else _value(v, text)
    text = args.get("constraints")
    if text:
        node = ast.parse(text, mode="eval").body
        if not isinstance(node, (ast.List, ast.Tuple)):
            raise ValueError("sketch constraints: the app can edit a written-out [...]")
        out["constraints"] = [_seq(e, text) for e in node.elts]
    return out
