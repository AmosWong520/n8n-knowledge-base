#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

ROOT = Path(".")
SRC_FILE = ROOT / "raw" / "n8n_nodes_library.json"
OUT_DIR = ROOT / "output"

def sanitize_filename(name: str) -> str:
    name = name or "unnamed_node"
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = name.replace(" ", "_")
    return name

def ensure_out_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

def values_list_to_expr(values: Union[List[Any], Any]) -> str:
    if not isinstance(values, (list, tuple)):
        values = [values]
    if len(values) == 1:
        v = values[0]
        return f"'{v}'" if isinstance(v, str) else str(v)
    return json.dumps(values, ensure_ascii=False)

def flatten_display_options_display(d: Dict[str, Any], prefix: str = "") -> List[Tuple[str, Any]]:
    """
    Flatten nested displayOptions dict into list of (key_path, values)
    e.g. {"resource": {"operation": ["create"]}} -> [("resource.operation", ["create"])]
    """
    pairs: List[Tuple[str, Any]] = []
    for k, v in d.items():
        if isinstance(v, dict):
            for subk, subv in flatten_display_options_display(v, prefix="").items() if False else []:
                pass
        if isinstance(v, dict):
            # nested dict: recurse
            for subk, subv in flatten_display_options_display(v, prefix=""):
                pairs.append((f"{k}.{subk}", subv))
        else:
            pairs.append((k if not prefix else f"{prefix}.{k}", v))
    return pairs

def flatten_display_options(d: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """
    Takes a displayOptions mapping (show/hide) and flattens into conditions
    Returns list of tuples (field_path, values)
    """
    out: List[Tuple[str, Any]] = []
    for k, v in d.items():
        if k in ("show", "hide") and isinstance(v, dict):
            for field, vals in v.items():
                if isinstance(vals, dict):
                    # nested dict like {"resource": {"operation": ["create"]}}
                    for sub_field, sub_vals in vals.items():
                        out.append((f"{field}.{sub_field}", sub_vals))
                else:
                    out.append((field, vals))
    return out

def display_options_to_rules(display_options: Dict[str, Any], target: str = None) -> List[str]:
    """
    Convert a displayOptions object (common in n8n nodes) into human-readable IF...THEN... rules.
    Produces rules for both 'show' and 'hide' cases.
    """
    rules: List[str] = []
    if not isinstance(display_options, dict):
        return rules
    # handle show/hide at top-level
    for mode in ("show", "hide"):
        if mode in display_options and isinstance(display_options[mode], dict):
            conds = []
            for field, val in display_options[mode].items():
                if isinstance(val, dict):
                    # nested dict: join nested conditions with AND
                    for sub_field, sub_vals in val.items():
                        expr = _single_condition_expr(f"{field}.{sub_field}", sub_vals)
                        conds.append(expr)
                else:
                    expr = _single_condition_expr(field, val)
                    conds.append(expr)
            if conds:
                cond_expr = " AND ".join(conds)
                action = "SHOW" if mode == "show" else "HIDE"
                target_desc = f" parameter '{target}'" if target else ""
                rules.append(f"IF {cond_expr} THEN {action}{target_desc}")
    return rules

def _single_condition_expr(field: str, val: Any) -> str:
    if isinstance(val, (list, tuple)):
        if len(val) == 1:
            v = val[0]
            return f"{field} == '{v}'" if isinstance(v, str) else f"{field} == {v}"
        return f"{field} IN {json.dumps(val, ensure_ascii=False)}"
    else:
        return f"{field} == '{val}'" if isinstance(val, str) else f"{field} == {val}"

def generate_ai_hint_for_param(name: str, param: Dict[str, Any]) -> str:
    """
    Heuristic rules to auto-generate ai_hint for a parameter based on its name and type.
    """
    ptype = (param.get("type") or "").lower()
    name_lower = (name or "").lower()
    hints: List[str] = []
    if ptype in ("fixedcollection", "fixed_collection", "fixedCollection", "collection"):
        hints.append("此参数为 fixedCollection，包含嵌套字段，不要扁平化。")
    if "url" in name_lower:
        hints.append("必须包含协议头 (http:// 或 https://)。支持表达式注入。")
    if name_lower in ("method",) or ptype == "options" and param.get("options"):
        hints.append("根据 REST 规范选择方法: 查询用 GET，创建用 POST，更新用 PUT/PATCH。")
    if ptype == "boolean" or name_lower.startswith("is") or name_lower.startswith("has"):
        hints.append("布尔值：true 或 false。")
    if "token" in name_lower or "auth" in name_lower:
        hints.append("与鉴权相关，注意不要在公共日志中输出敏感信息。")
    if not hints:
        # default short hint if description exists
        desc = param.get("description") or param.get("note") or ""
        if desc:
            return desc
        return ""
    return " ".join(hints)

def find_nodes_in_object(obj: Any) -> List[Dict[str, Any]]:
    """
    Try to locate node objects within a loaded JSON file. Common shapes:
    - top-level list of nodes
    - dict with 'nodes' or 'items' or similar
    - single node dict
    """
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    if isinstance(obj, dict):
        # common container keys
        for key in ("nodes", "items", "resources", "elements"):
            if key in obj and isinstance(obj[key], list):
                return [o for o in obj[key] if isinstance(o, dict)]
        # assume this dict itself might be a node (has name or displayOptions or properties)
        if any(k in obj for k in ("name", "displayOptions", "properties", "parameters", "credentials")):
            return [obj]
    return []

def clean_parameter(param: Dict[str, Any], name: str = None) -> Dict[str, Any]:
    p = dict(param)  # shallow copy
    if name:
        p.setdefault("name", name)
    # ensure ai_hint
    if not p.get("ai_hint"):
        generated = generate_ai_hint_for_param(p.get("name") or name, p)
        if generated:
            p["ai_hint"] = generated
    return p

def collect_conditional_rules_from_params(params: Union[Dict[str, Any], List[Any]]) -> List[str]:
    rules: List[str] = []
    if isinstance(params, dict):
        iterator = params.items()
    elif isinstance(params, list):
        # list of parameter dicts or collections
        iterator = enumerate(params)
    else:
        return rules
    for key, val in iterator:
        # val might be a dict defining a parameter
        if isinstance(val, dict):
            # parameter-level displayOptions
            display = val.get("displayOptions") or val.get("display_options") or val.get("display")
            param_name = val.get("name") or (key if isinstance(key, str) else None)
            if display:
                rules.extend(display_options_to_rules(display, target=param_name))
            # nested parameters in fixedCollection / collection
            if val.get("type") and "collection" in str(val.get("type")).lower():
                # attempt to find nested values
                for nested_key in ("options", "values", "fields", "properties", "collection"):
                    nested = val.get(nested_key)
                    if nested and isinstance(nested, (list, dict)):
                        rules.extend(collect_conditional_rules_from_params(nested))
            # also check for nested "options" which may be a list of groups
            if isinstance(val.get("options"), list):
                for opt in val.get("options"):
                    if isinstance(opt, dict):
                        rules.extend(collect_conditional_rules_from_params(opt.get("values") or opt.get("options") or opt.get("fields") or opt.get("properties") or []))
    return rules

def clean_node(node: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    # map top-level identifiers
    cleaned["node_id"] = node.get("node_id") or node.get("id") or node.get("name") or node.get("type")
    cleaned["name"] = node.get("name") or cleaned["node_id"]
    # preserve version or default
    cleaned["version"] = node.get("version") or node.get("versionId") or 1
    # semantic context / description
    cleaned["semantic_context"] = node.get("description") or node.get("summary") or node.get("note") or ""
    # parameters: try to canonicalize into a dict of name->param
    raw_params = node.get("properties") or node.get("parameters") or node.get("options") or node.get("inputs") or node.get("parametersList") or node.get("fields") or node.get("attributes") or node.get("props") or node.get("params")
    params_out: Dict[str, Any] = {}
    if isinstance(raw_params, dict):
        for pname, pval in raw_params.items():
            params_out[pname] = clean_parameter(pval, name=pname)
    elif isinstance(raw_params, list):
        # list of param dicts
        for p in raw_params:
            if isinstance(p, dict):
                pname = p.get("name") or p.get("key") or p.get("id")
                if not pname:
                    # generate a unique placeholder name
                    pname = p.get("label") or ("param_" + str(len(params_out) + 1))
                params_out[pname] = clean_parameter(p, name=pname)
    else:
        # try to pull 'parameters' key from node children
        params_out = {}
    cleaned["parameters"] = params_out
    # configuration logic
    config_logic: Dict[str, Any] = {}
    # required sequence - try to infer from an ordered list or from a known field
    if isinstance(raw_params, list):
        config_logic["required_sequence"] = [p.get("name") or p.get("key") or p.get("id") for p in raw_params if isinstance(p, dict) and (p.get("name") or p.get("key") or p.get("id"))]
    else:
        config_logic["required_sequence"] = list(params_out.keys())
    # collect conditional rules from parameters and node-level displayOptions
    rules: List[str] = []
    # node-level displayOptions
    node_display = node.get("displayOptions") or node.get("display_options") or node.get("display")
    if node_display:
        rules.extend(display_options_to_rules(node_display, target=node.get("name")))
    # parameter-level
    rules.extend(collect_conditional_rules_from_params(raw_params or []))
    # deduplicate while preserving order
    seen = set()
    deduped_rules = []
    for r in rules:
        if r not in seen:
            deduped_rules.append(r)
            seen.add(r)
    config_logic["conditional_rules"] = deduped_rules
    cleaned["configuration_logic"] = config_logic
    # preserve an example snippet if present
    if node.get("workflow_json_snippet"):
        cleaned["workflow_json_snippet"] = node.get("workflow_json_snippet")
    return cleaned

def process_all_sources() -> None:
    ensure_out_dir()
    master: List[Dict[str, Any]] = []
    found_any = False
    if not SRC_FILE.exists():
        print(f"Source file {SRC_FILE} not found.")
        return
    try:
        text = SRC_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as e:
        print(f"Failed to load source file {SRC_FILE}: {e}")
        return
    # data expected to be a list of node objects
    if isinstance(data, list):
        nodes = [n for n in data if isinstance(n, dict)]
    else:
        nodes = find_nodes_in_object(data)
        if not nodes and isinstance(data, dict):
            nodes = [data]
    for idx, node in enumerate(nodes, start=1):
        try:
            cleaned = clean_node(node)
            name = sanitize_filename(cleaned.get("name") or cleaned.get("node_id") or f"node_{idx}")
            out_path = OUT_DIR / f"{name}.json"
            out_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
            master.append(cleaned)
            found_any = True
        except Exception as e:
            print(f"Failed to process node index {idx}: {e}")
    # write master file
    master_path = ROOT / "n8n_nodes_master.json"
    master_path.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")
    if found_any:
        print(f"Processed {len(master)} nodes. Output written to {OUT_DIR} and {master_path}")
    else:
        print("No nodes were found in source files.")

if __name__ == "__main__":
    process_all_sources()


