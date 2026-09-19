#!/usr/bin/env python3
"""
Handoff schemas between agents.

Andre's spec called this the part that breaks first if it's loose, and he's
right: subagents here are stateless and return free text unless something
forces structure. So every handoff is validated before it is accepted, and a
malformed payload is REJECTED AND LOGGED rather than passed along half-read.
A silently-dropped field is how a stop price goes missing.

Dependency-free on purpose. The device's network egress is restricted, so a
pip install of jsonschema is not guaranteed to work at 7am. These schemas are
fixed and small; a hand-rolled checker is more reliable here than a dependency
that might not be installable.
"""

import json
import sys

# type, required, allowed-values
SCHEMAS = {
    # News Agent -> Knowledge Base Agent, and News Agent -> Orchestrator
    "NewsEvent": {
        "ticker":                ("str",   False, None),
        "event_type":            ("str",   True,  ["earnings", "guidance", "analyst", "filing", "ma",
                                                   "regulatory", "sector", "macro", "other"]),
        "headline":              ("str",   True,  None),
        "summary":               ("str",   True,  None),
        "why_material":          ("str",   True,  None),
        "materiality_breakdown": ("dict",  True,  None),
        "materiality_total":     ("int",   True,  None),
        "contradicts":           ("str",   False, None),
        "source":                ("str",   True,  None),
        "source_url":            ("str",   False, None),
        "published_at":          ("str",   True,  None),
    },
    # Technical Analyst -> Orchestrator
    "TechnicalSignal": {
        "ticker":            ("str",   True,  None),
        "signal":            ("str",   True,  ["buy", "hold", "sell"]),
        "timeframe":         ("str",   True,  None),
        "observed":          ("list",  True,  None),   # what it saw
        "inferred":          ("list",  True,  None),   # what it concluded from that
        "entry_zone":        ("list",  True,  None),   # [low, high]
        "stop":              ("float", True,  None),
        "targets":           ("list",  True,  None),
        "confidence":        ("str",   True,  ["low", "medium", "high"]),
        "invalidation":      ("str",   True,  None),   # the specific condition that proves it wrong
        "chart_path":        ("str",   False, None),
        "options_context":   ("dict",  False, None),
    },
    # Orchestrator -> Execution Agent
    "TradeProposal": {
        "proposal_id":       ("str",   True,  None),
        "ticker":            ("str",   True,  None),
        "side":              ("str",   True,  ["BUY", "SELL"]),
        "instrument":        ("str",   True,  ["EQUITY", "OPTION"]),
        "quantity":          ("float", True,  None),
        "order_type":        ("str",   True,  ["LIMIT"]),
        "limit_price":       ("float", True,  None),
        "stop_price":        ("float", True,  None),
        "targets":           ("list",  True,  None),
        "time_in_force":     ("str",   True,  ["DAY", "GTC"]),
        "rationale":         ("str",   True,  None),
        "sources":           ("list",  True,  None),   # which agents contributed
        "is_averaging_down": ("bool",  True,  None),
        "averaging_rationale": ("str", False, None),
    },
    # Execution Agent -> Knowledge Base Agent
    "OrderRecord": {
        "proposal_id":  ("str",   True,  None),
        "mode":         ("str",   True,  ["paper", "live"]),
        "status":       ("str",   True,  ["proposed", "rejected", "awaiting_confirm",
                                          "filled", "cancelled", "expired"]),
        "ticker":       ("str",   True,  None),
        "reject_reason": ("str",  False, None),
        "broker_ref":   ("str",   False, None),
        "max_loss_dollars": ("float", True, None),
    },
    # Knowledge Base Agent -> Orchestrator
    "StalenessReport": {
        "generated_at":             ("str",  True, None),
        "stale_theses":             ("list", True, None),
        "stale_levels":             ("list", True, None),
        "positions_without_thesis": ("list", True, None),
        "unscored_alpha_calls":     ("list", True, None),
        "clean":                    ("bool", True, None),
    },
    # Parsed out of Kevin's Alpha Report
    "AlphaAlert": {
        "ticker":      ("str",   True,  None),
        "direction":   ("str",   True,  ["long", "short", "close", "hold"]),
        "instrument":  ("str",   False, ["equity", "option"]),
        "entry_hint":  ("float", False, None),
        "stop_hint":   ("float", False, None),
        "target_hint": ("float", False, None),
        "horizon":     ("str",   False, None),
        "raw_excerpt": ("str",   True,  None),
    },
}

TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


def validate(name, payload):
    """Returns (ok, errors). Never raises on bad input -- callers log the errors."""
    if name not in SCHEMAS:
        return False, [f"unknown schema '{name}'"]
    spec = SCHEMAS[name]
    errors = []

    for field, (typ, required, allowed) in spec.items():
        if field not in payload or payload[field] is None:
            if required:
                errors.append(f"missing required field '{field}'")
            continue
        val = payload[field]
        if not isinstance(val, TYPES[typ]) or (typ != "bool" and isinstance(val, bool)):
            errors.append(f"field '{field}' should be {typ}, got {type(val).__name__}")
            continue
        if allowed and val not in allowed:
            errors.append(f"field '{field}' must be one of {allowed}, got '{val}'")

    for field in payload:
        if field not in spec and not field.startswith("_"):
            errors.append(f"unexpected field '{field}' (typo, or schema needs updating)")

    # Cross-field rules that a type check alone would let through.
    if name == "TechnicalSignal" and not errors:
        ez = payload["entry_zone"]
        if len(ez) != 2 or ez[0] > ez[1]:
            errors.append("entry_zone must be [low, high] with low <= high")
        if payload["signal"] == "buy" and payload["stop"] >= ez[0]:
            errors.append("a buy signal's stop must sit below the entry zone")
        if payload["signal"] == "sell" and payload["stop"] <= ez[1]:
            errors.append("a sell signal's stop must sit above the entry zone")

    if name == "TradeProposal" and not errors:
        if payload["is_averaging_down"] and not payload.get("averaging_rationale"):
            errors.append("averaging down requires an explicit averaging_rationale")
        if payload["side"] == "BUY" and payload["stop_price"] >= payload["limit_price"]:
            errors.append("a buy proposal's stop must sit below the limit price")

    return (len(errors) == 0), errors


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: schemas.py <SchemaName> <json>"}))
        sys.exit(2)
    try:
        payload = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "errors": [f"payload is not valid JSON: {e}"]}))
        sys.exit(2)
    ok, errors = validate(sys.argv[1], payload)
    print(json.dumps({"ok": ok, "schema": sys.argv[1], "errors": errors}, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
