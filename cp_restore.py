import argparse
import base64
import gzip
import io
import json
import struct
import sys

SLOTS = {0: "armor.feet", 1: "armor.legs", 2: "armor.chest", 3: "armor.head", 4: "weapon.mainhand", 5: "weapon.offhand"}
OPS = {0: "add_value", 1: "add_multiplied_base", 2: "add_multiplied_total"}
SUFFIX = {1: "b", 2: "s", 4: "L", 5: "f", 6: "d"}


class Num:
    def __init__(self, value, tag):
        self.value = value
        self.tag = tag

    def snbt(self):
        if self.tag in (5, 6):
            return repr(float(self.value)) + SUFFIX[self.tag]
        return str(self.value) + SUFFIX.get(self.tag, "")

    def json(self):
        return self.value


def _rd(f, fmt):
    return struct.unpack(">" + fmt, f.read(struct.calcsize(fmt)))[0]


def _rstr(f):
    return f.read(_rd(f, "H")).decode("utf-8", "replace")


def _payload(f, t):
    if t in (1, 2, 3, 4, 5, 6):
        return Num(_rd(f, {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[t]), t)
    if t == 7:
        return ("B", [Num(b if b < 128 else b - 256, 1) for b in f.read(_rd(f, "i"))])
    if t == 8:
        return _rstr(f)
    if t == 9:
        et = _rd(f, "b")
        return [_payload(f, et) for _ in range(_rd(f, "i"))]
    if t == 10:
        d = {}
        while True:
            tt = _rd(f, "b")
            if tt == 0:
                return d
            k = _rstr(f)
            d[k] = _payload(f, tt)
    if t == 11:
        return ("I", [Num(_rd(f, "i"), 3) for _ in range(_rd(f, "i"))])
    if t == 12:
        return ("L", [Num(_rd(f, "q"), 4) for _ in range(_rd(f, "i"))])
    raise ValueError("unknown NBT tag %d" % t)


def nbt_from_b64(b64):
    f = io.BytesIO(gzip.decompress(base64.b64decode(b64)))
    t = _rd(f, "b")
    _rstr(f)
    return _payload(f, t)


def snbt(v):
    if isinstance(v, Num):
        return v.snbt()
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ",".join("%s:%s" % (json.dumps(k), snbt(x)) for k, x in v.items()) + "}"
    if isinstance(v, tuple):
        return "[%s;%s]" % (v[0], ",".join(snbt(x) for x in v[1]))
    if isinstance(v, list):
        return "[" + ",".join(snbt(x) for x in v) + "]"
    if isinstance(v, bool):
        return "1b" if v else "0b"
    if isinstance(v, float):
        return repr(v) + "d"
    return str(v)


def to_json(v):
    if isinstance(v, Num):
        return {"$nbt": v.snbt()}
    if isinstance(v, dict):
        return {k: to_json(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return {"$nbt": snbt(v)}
    if isinstance(v, list):
        return [to_json(x) for x in v]
    return v


def from_json(v):
    if isinstance(v, dict):
        if set(v) == {"$nbt"}:
            return Raw(v["$nbt"])
        return {k: from_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [from_json(x) for x in v]
    return v


class Raw(Num):
    def __init__(self, text):
        super().__init__(text, 0)

    def snbt(self):
        return self.value


def _java_to_py(o):
    import javaobj.v1 as jo
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    if isinstance(o, dict):
        return {str(_java_to_py(k)): _java_to_py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_java_to_py(x) for x in o]
    cls = getattr(o, "classdesc", None)
    name = cls.name if cls else type(o).__name__
    if name.endswith("Map$SerializedForm"):
        return {str(_java_to_py(k)): _java_to_py(v) for k, v in zip(o.keys, o.values)}
    if name in ("java.lang.Integer", "java.lang.Double", "java.lang.Long", "java.lang.Float",
                "java.lang.Short", "java.lang.Byte", "java.lang.Boolean"):
        return o.value
    if name.startswith("["):
        return [_java_to_py(x) for x in o]
    fields = {f: _java_to_py(getattr(o, f)) for f in getattr(o, "__dict__", {})
              if not f.startswith("_") and f not in ("classdesc", "annotations")}
    return {"__class": name, **fields}


def flatten(meta):
    flat = {}
    for part in meta if isinstance(meta, list) else [meta]:
        for d in part if isinstance(part, list) else [part]:
            if isinstance(d, dict):
                flat.update(d)
    for k in ("unhandled", "custom"):
        if isinstance(flat.get(k), str):
            flat[k] = to_json(nbt_from_b64(flat[k]))
    return flat


def cmd_decode(args):
    import javaobj.v1 as jo
    out = []
    for n, line in enumerate(open(args.rows, encoding="utf-8"), 1):
        line = line.rstrip("\r\n")
        if not line:
            continue
        cols = line.split("\t")
        if len(cols) != 5:
            sys.exit("line %d: expected 5 tab-separated columns, got %d" % (n, len(cols)))
        rowid, loc, material, amount, hx = cols
        entry = {"rowid": int(rowid), "loc": [int(c) for c in loc.split()], "material": material, "amount": int(amount)}
        try:
            entry["meta"] = flatten(_java_to_py(jo.loads(bytes.fromhex(hx)))) if hx and hx != "NULL" else {}
        except Exception as e:
            entry["meta"] = {}
            entry["error"] = repr(e)
        out.append(entry)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = sum(1 for e in out if "error" in e)
    print("decoded %d items, %d errors -> %s" % (len(out), bad, args.out))
    return 1 if bad else 0


def item_id(material, namespaces):
    ns, _, name = material.partition(":")
    for prefix in sorted(namespaces, key=len, reverse=True):
        if name.startswith(prefix + "_"):
            return prefix + ":" + name[len(prefix) + 1:]
    return material


def components(meta):
    c = []
    if meta.get("Damage"):
        c.append("damage=%d" % meta["Damage"])
    if meta.get("enchants"):
        c.append("enchantments={levels:{%s}}" % ",".join('"%s":%d' % (k, v) for k, v in meta["enchants"].items()))
    if meta.get("repair-cost"):
        c.append("repair_cost=%d" % meta["repair-cost"])
    if isinstance(meta.get("custom"), dict):
        c.append("custom_data=%s" % snbt(from_json(meta["custom"])))
    if meta.get("modifiers"):
        mods = []
        for m in meta["modifiers"]:
            for attr, d in m.items():
                mods.append('{type:"%s",id:"%s",amount:%sd,operation:"%s",slot:"%s"}'
                            % (attr, d["key"], repr(float(d["amount"])), OPS[d["operation"]], d["slot"]))
        c.append("attribute_modifiers={modifiers:[%s]}" % ",".join(mods))
    if isinstance(meta.get("unhandled"), dict):
        for k, v in from_json(meta["unhandled"]).items():
            c.append("%s=%s" % (k, snbt(v)))
    return "[" + ",".join(c) + "]" if c else ""


def cmd_commands(args):
    items = json.load(open(args.items, encoding="utf-8"))
    namespaces = [n for n in args.namespaces.split(",") if n]
    out = []
    for it in items:
        if "error" in it:
            print("skip rowid %s: %s" % (it["rowid"], it["error"]), file=sys.stderr)
            continue
        stack = item_id(it["material"], namespaces) + components(it.get("meta", {}))
        if args.mode == "give":
            out.append("give %s %s %d" % (args.player, stack, it["amount"]))
            continue
        slot = SLOTS.get(it.get("meta", {}).get("slot"))
        if slot is None:
            print("skip rowid %s: no armor stand slot" % it["rowid"], file=sys.stderr)
            continue
        x, y, z = it["loc"]
        sel = "@e[type=minecraft:armor_stand,distance=..%s,limit=1,sort=nearest]" % args.radius
        guard = "" if args.overwrite else "unless items entity @s %s * " % slot
        out.append("execute in %s positioned %s %d %s as %s %srun item replace entity @s %s with %s"
                   % (args.world, x + 0.5, y, z + 0.5, sel, guard, slot, stack))
    text = "\n".join(out) + ("\n" if out else "")
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print("%d commands -> %s" % (len(out), args.out))
    else:
        sys.stdout.write(text)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="cp-restore", description="Turn CoreProtect container records back into Minecraft 1.21 item commands.")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode a TSV export of co_container rows into JSON")
    d.add_argument("rows", help="TSV: rowid, 'x y z', material, amount, HEX(metadata)")
    d.add_argument("out", help="output JSON file")
    c = sub.add_parser("commands", help="generate commands from decoded JSON")
    c.add_argument("items", help="JSON written by decode")
    c.add_argument("--mode", choices=("stand", "give"), default="stand")
    c.add_argument("--player", help="target of /give in give mode")
    c.add_argument("--world", default="minecraft:overworld", help="dimension for stand mode")
    c.add_argument("--radius", default="1.2", help="how far from the logged block the stand may be")
    c.add_argument("--namespaces", default="", help="comma-separated mod ids to split from Bukkit material names, e.g. twilightforest,aether")
    c.add_argument("--overwrite", action="store_true", help="replace slots that are not empty")
    c.add_argument("--out", help="write commands to this file instead of stdout")
    args = p.parse_args(argv)
    if args.cmd == "commands" and args.mode == "give" and not args.player:
        p.error("--player is required with --mode give")
    return cmd_decode(args) if args.cmd == "decode" else cmd_commands(args)


if __name__ == "__main__":
    sys.exit(main())
