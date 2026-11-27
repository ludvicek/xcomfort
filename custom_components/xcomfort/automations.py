"""The controller's automations: macros, scene actions and the planner.

Plain functions without Home Assistant, so they can be tested on their own:
normalize() turns what the SHC sends into a stable snapshot, fingerprint()
summarises it, diff() says what changed between two snapshots, and the
describe_*() functions write a macro out as text.

Read only. Nothing here changes anything on the controller.
"""
import hashlib
import json

# How often the automations are read, in minutes. They change only when the
# installation is reconfigured, and every macro costs three requests.
AUTOMATION_REFRESH_MINUTES = 15

EVENT_AUTOMATIONS_CHANGED = "xcomfort_automations_changed"

# The planner calls that answer on every controller seen so far. All of them
# returned empty results, so what a filled planner looks like is not known yet.
PLANNER_CALLS = (
    ("agenda", "Planner/getAgendaEvents", ["", ""]),
    ("planners", "Planner/getPlannersWithMacro", ["", ""]),
    ("groups", "Planner/getPlannerEventGroups", ["", "", ""]),
)

# What defines a trigger, condition or operation. Runtime fields such as
# "triggered" or "status" are left out, so they do not count as a change.
TRIGGER_KEYS = ("suid", "name", "prop", "oper", "value", "unit")
CONDITION_KEYS = ("suid", "name", "prop", "oper", "value", "unit", "condType", "condOper")
OPERATION_KEYS = ("suid", "name", "type", "cmd", "cmdType", "opType", "value", "unit",
                  "delay", "subject", "text", "recipient")

OPERATORS = {"equals": "=", "notEquals": "!=", "greater": ">", "less": "<"}
MESSAGE_TYPES = {"Mail": "send e-mail", "APN": "send app notification",
                 "GCM": "send app notification"}


def _clean(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return " ".join(str(value).split())


def _masked(value):
    """A short hash in place of a personal value such as an e-mail address.

    A change still shows up in the fingerprint, but the value itself is not
    stored or shown anywhere.
    """
    if not value:
        return value
    return "hash:" + hashlib.sha256(str(value).encode()).hexdigest()[:12]


def _items(items, keys):
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not any(item.get(key) for key in keys):
            continue
        entry = {key: _clean(item.get(key)) for key in keys if item.get(key) not in (None, "")}
        if "recipient" in entry:
            entry["recipient"] = _masked(entry["recipient"])
        result.append(entry)
    return result


def _enabled(value):
    return value is True or str(value).lower() == "true"


def normalize(raw):
    """A stable snapshot of what refresh_automations() read."""
    macros = {}
    for macro_id, macro in (raw.get("macros") or {}).items():
        macros[macro_id] = {
            "name": _clean(macro.get("name")) or macro_id,
            "zone": macro.get("zone"),
            "enabled": _enabled(macro.get("enabled")),
            "triggers": _items(macro.get("triggers"), TRIGGER_KEYS),
            "conditions": _items(macro.get("conditions"), CONDITION_KEYS),
            "operations": _items(macro.get("operations"), OPERATION_KEYS),
        }
    scenes = {}
    for scene_id, scene in (raw.get("scenes") or {}).items():
        scenes[scene_id] = {
            "name": _clean(scene.get("name")) or scene_id,
            "zone": scene.get("zone"),
            "enabled": _enabled(scene.get("enabled")),
            "operations": _items(scene.get("operations"), OPERATION_KEYS),
        }
    return {"macros": macros, "scenes": scenes, "planner": raw.get("planner") or {}}


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


# --- Text ----------------------------------------------------------------------

def _value(item):
    value, unit = item.get("value"), item.get("unit")
    text = "" if value is None else str(value)
    return f"{text} {unit}".strip() if unit else text


def _subject(item, resolve):
    name = item.get("name") or item.get("suid") or "?"
    entity_id = resolve(item.get("suid")) if resolve and item.get("suid") else None
    return f"{name} [{entity_id}]" if entity_id else name


def describe_trigger(item, resolve=None):
    oper = OPERATORS.get(item.get("oper"), item.get("oper") or "")
    return f"{_subject(item, resolve)} {item.get('prop', '')} {oper} {_value(item)}".replace("  ", " ").strip()


describe_condition = describe_trigger


def describe_operation(item, resolve=None):
    if item.get("type") in MESSAGE_TYPES:
        text = f"{MESSAGE_TYPES[item['type']]} '{item.get('text', '')}'"
    else:
        text = f"{_subject(item, resolve)}: {item.get('cmd', '?')}"
        if item.get("value") not in (None, 0, "0", ""):
            text += f" {_value(item)}"
    if item.get("delay"):
        text += f" after {item['delay']} s"
    return text


def describe_macro(macro, resolve=None):
    """"When <trigger> if <condition> then <operation>, <operation>"."""
    parts = []
    if macro.get("triggers"):
        parts.append("When " + " or ".join(describe_trigger(t, resolve) for t in macro["triggers"]))
    if macro.get("conditions"):
        parts.append("if " + " and ".join(describe_condition(c, resolve) for c in macro["conditions"]))
    operations = ", ".join(describe_operation(o, resolve) for o in macro.get("operations", []))
    parts.append(("then " if parts else "") + (operations or "nothing"))
    return " ".join(parts)


# --- Differences ---------------------------------------------------------------

def _key(kind, item):
    if kind == "operations":
        return item.get("suid") or (item.get("type"), item.get("text"))
    return (item.get("suid"), item.get("prop"))


_DESCRIBE = {"triggers": describe_trigger, "conditions": describe_condition,
             "operations": describe_operation}
_LABEL = {"triggers": "trigger", "conditions": "condition", "operations": "action"}


def _diff_items(kind, old_items, new_items):
    describe, label = _DESCRIBE[kind], _LABEL[kind]
    details = []
    old_by_key = {}
    for item in old_items:
        old_by_key.setdefault(_key(kind, item), []).append(item)
    for item in new_items:
        matches = old_by_key.get(_key(kind, item))
        if matches:
            before = matches.pop(0)
            if before != item:
                if before.get("recipient") != item.get("recipient") and \
                        {k: v for k, v in before.items() if k != "recipient"} == \
                        {k: v for k, v in item.items() if k != "recipient"}:
                    details.append(f"{label} {describe(item)}: e-mail recipient changed")
                else:
                    details.append(f"{label} changed: {describe(before)} -> {describe(item)}")
        else:
            details.append(f"{label} added: {describe(item)}")
    for remaining in old_by_key.values():
        details.extend(f"{label} removed: {describe(item)}" for item in remaining)
    return details


def _diff_group(kind, old, new, parts):
    changes = []
    for item_id in sorted(set(old) | set(new)):
        before, after = old.get(item_id), new.get(item_id)
        base = {"kind": kind, "id": item_id,
                "name": (after or before)["name"], "zone": (after or before).get("zone")}
        if before is None:
            changes.append({**base, "action": "added", "details": [
                describe_macro(after) if kind == "macro" else
                ", ".join(describe_operation(o) for o in after["operations"]) or "no actions"]})
            continue
        if after is None:
            changes.append({**base, "action": "removed", "details": []})
            continue
        details = []
        if before["name"] != after["name"]:
            details.append(f"renamed: {before['name']} -> {after['name']}")
        if before["enabled"] != after["enabled"]:
            details.append("enabled" if after["enabled"] else "disabled")
        if before.get("zone") != after.get("zone"):
            details.append(f"moved from zone {before.get('zone')} to {after.get('zone')}")
        for part in parts:
            details.extend(_diff_items(part, before.get(part, []), after.get(part, [])))
        if details:
            changes.append({**base, "action": "changed", "details": details})
    return changes


def diff(old, new):
    """What changed from snapshot old to snapshot new, as a list of changes."""
    changes = _diff_group("macro", old.get("macros", {}), new.get("macros", {}),
                          ("triggers", "conditions", "operations"))
    changes += _diff_group("scene", old.get("scenes", {}), new.get("scenes", {}),
                           ("operations",))
    old_planner, new_planner = old.get("planner", {}), new.get("planner", {})
    if old_planner != new_planner:
        details = []
        for key in sorted(set(old_planner) | set(new_planner)):
            before, after = old_planner.get(key), new_planner.get(key)
            if before != after:
                details.append(f"{key}: {len(before or [])} -> {len(after or [])} entries")
        changes.append({"kind": "planner", "action": "changed", "id": "planner",
                        "name": "Planner", "zone": None, "details": details})
    return changes


def summarize(changes):
    """One line per change, for logs and notifications."""
    lines = []
    for change in changes:
        head = f"{change['kind']} {change['name']} {change['action']}"
        lines.append(head + (": " + "; ".join(change["details"]) if change["details"] else ""))
    return lines
