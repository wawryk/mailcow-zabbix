#!/usr/bin/env python3
"""Statyczna walidacja wygenerowanego szablonu (bez serwera Zabbix):
UUIDv4 i ich unikalnosc, unikalnosc kluczy, referencje kluczy w triggerach/wykresach,
makra uzywane vs zdefiniowane, wartosci enumow."""
import re
import sys
import yaml

d = yaml.safe_load(open(sys.argv[1]))["zabbix_export"]
t = d["templates"][0]
T = t["template"]
errors = []
uuids = []

def walk(o):
    if isinstance(o, dict):
        if "uuid" in o:
            uuids.append(o["uuid"])
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
walk(d)
for u in uuids:
    if not re.fullmatch(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", u):
        errors.append(f"bad uuid {u}")
if len(uuids) != len(set(uuids)):
    errors.append("duplicate uuids")

keys, proto_keys, exprs, gitems, names = [], [], [], [], []
for it in t["items"]:
    keys.append(it["key"])
    for tr in it.get("triggers", []):
        exprs.append((tr["expression"], set(keys)))
        names.append(tr["name"])
for r in t["discovery_rules"]:
    keys.append(r["key"])
    for p in r["item_prototypes"]:
        proto_keys.append(p["key"])
        for tr in p.get("trigger_prototypes", []):
            exprs.append((tr["expression"], None))
            names.append(tr["name"])
    for g in r.get("graph_prototypes", []):
        gitems += [gi["item"]["key"] for gi in g["graph_items"]]
for g in d.get("graphs", []):
    gitems += [gi["item"]["key"] for gi in g["graph_items"]]

allkeys = keys + proto_keys
for k in set(allkeys):
    if allkeys.count(k) > 1:
        errors.append(f"duplicate key {k}")
for e, _ in exprs:
    for ref in re.findall(r"/" + re.escape(T) + r"/(.+?)(?=[,)])(?![^\[]*\])", e):
        pass
    # wyciagnij klucze: /T/<key> konczy sie na ',' lub ')' poza nawiasami []
    i = 0
    while True:
        i = e.find("/" + T + "/", i)
        if i < 0:
            break
        j = i + len(T) + 2; depth = 0; k = j
        while k < len(e):
            c = e[k]
            if c == "[": depth += 1
            elif c == "]": depth -= 1
            elif c in ",)" and depth == 0: break
            k += 1
        key = e[j:k]
        if key not in allkeys:
            errors.append(f"trigger references unknown key {key!r}")
        i = k
for k in gitems:
    if k not in allkeys:
        errors.append(f"graph references unknown key {k!r}")

for it in t["items"]:
    if it.get("type") == "DEPENDENT" and it["master_item"]["key"] not in keys:
        errors.append(f"dependent item {it['key']!r}: unknown master {it['master_item']['key']!r}")
graph_names = {g["name"] for g in d.get("graphs", [])}
proto_graph_names = {g["name"] for r in t["discovery_rules"] for g in r.get("graph_prototypes", [])}
for db in t.get("dashboards", []):
    for pg in db["pages"]:
        for w in pg["widgets"]:
            for f in w["fields"]:
                v = f["value"]
                if f["type"] == "GRAPH" and v["name"] not in graph_names:
                    errors.append(f"widget {w['name']!r}: unknown graph {v['name']!r}")
                elif f["type"] == "GRAPH_PROTOTYPE" and v["name"] not in proto_graph_names:
                    errors.append(f"widget {w['name']!r}: unknown graph prototype {v['name']!r}")
                elif f["type"] == "ITEM" and v["key"] not in keys:
                    errors.append(f"widget {w['name']!r}: unknown item {v['key']!r}")
            if int(w.get("x", 0)) + int(w["width"]) > 24:
                errors.append(f"widget {w['name']!r} wider than 24-column grid")

defined = {m["macro"] for m in t["macros"]}
text = yaml.dump(d)
used = set(re.findall(r"\{\$[A-Z0-9_.]+\}", text))
for m in sorted(used - defined):
    errors.append(f"macro used but not defined: {m}")
for m in sorted(defined - used):
    print(f"note: macro defined but unused: {m}")

prios = {"NOT_CLASSIFIED", "INFO", "WARNING", "AVERAGE", "HIGH", "DISASTER"}
def chk(o):
    if isinstance(o, dict):
        if "priority" in o and o["priority"] not in prios:
            errors.append(f"bad priority {o['priority']}")
        if "value_type" in o and o["value_type"] not in {"FLOAT", "CHAR", "LOG", "TEXT", "UNSIGNED"}:
            errors.append(f"bad value_type {o['value_type']}")
        for v in o.values(): chk(v)
    elif isinstance(o, list):
        for v in o: chk(v)
chk(d)
if len(names) != len(set(names)):
    errors.append("duplicate trigger names")

print(f"items={len(t['items'])} lld={len(t['discovery_rules'])} prototypes={len(proto_keys)} "
      f"triggers={len(names)} graphs={len(d.get('graphs', []))} macros={len(defined)} uuids={len(uuids)}")
if errors:
    print("ERRORS:"); [print(" -", e) for e in errors]; sys.exit(1)
print("OK")
