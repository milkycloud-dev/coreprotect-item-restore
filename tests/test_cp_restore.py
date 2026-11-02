import base64
import gzip
import io
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cp_restore


def nbt_compound(entries):
    b = io.BytesIO()
    b.write(struct.pack(">b", 10))
    b.write(struct.pack(">H", 0))
    for tag, key, value in entries:
        b.write(struct.pack(">b", tag))
        kb = key.encode()
        b.write(struct.pack(">H", len(kb)))
        b.write(kb)
        if tag == 5:
            b.write(struct.pack(">f", value))
        elif tag == 6:
            b.write(struct.pack(">d", value))
        elif tag == 3:
            b.write(struct.pack(">i", value))
        elif tag == 10:
            b.write(struct.pack(">b", 0))
    b.write(struct.pack(">b", 0))
    return base64.b64encode(gzip.compress(b.getvalue())).decode()


class NbtTypes(unittest.TestCase):
    def test_float_double_int_and_unit(self):
        v = cp_restore.nbt_from_b64(nbt_compound([(5, "a", 0.3), (6, "b", 1.4), (3, "c", 5), (10, "d", None)]))
        self.assertEqual(cp_restore.snbt(v["a"]), "0.30000001192092896f")
        self.assertEqual(cp_restore.snbt(v["b"]), "1.4d")
        self.assertEqual(cp_restore.snbt(v["c"]), "5")
        self.assertEqual(cp_restore.snbt(v["d"]), "{}")

    def test_json_round_trip_keeps_types(self):
        v = cp_restore.nbt_from_b64(nbt_compound([(5, "x", 2.5), (3, "n", 7)]))
        back = cp_restore.from_json(json.loads(json.dumps(cp_restore.to_json(v))))
        self.assertEqual(cp_restore.snbt(back), '{"x":2.5f,"n":7}')


class Commands(unittest.TestCase):
    def run_commands(self, items, *extra):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "items.json")
            out = os.path.join(d, "cmds.txt")
            json.dump(items, open(src, "w", encoding="utf-8"))
            cp_restore.main(["commands", src, "--out", out, *extra])
            return open(out, encoding="utf-8").read().splitlines()

    def test_stand_command_with_enchants_damage_and_guard(self):
        items = [{"rowid": 1, "loc": [158, 86, -188], "material": "minecraft:diamond_boots", "amount": 1,
                  "meta": {"Damage": 53, "enchants": {"minecraft:protection": 4}, "repair-cost": 7, "slot": 0}}]
        cmds = self.run_commands(items)
        self.assertEqual(cmds, ['execute in minecraft:overworld positioned 158.5 86 -187.5 as @e[type=minecraft:armor_stand,distance=..1.2,limit=1,sort=nearest] '
                                'unless items entity @s armor.feet * run item replace entity @s armor.feet with '
                                'minecraft:diamond_boots[damage=53,enchantments={levels:{"minecraft:protection":4}},repair_cost=7]'])

    def test_modded_namespace_and_components(self):
        unhandled = cp_restore.to_json(cp_restore.nbt_from_b64(nbt_compound([(6, "twilightforest:straight_ahead_multiplier", 1.4)])))
        items = [{"rowid": 2, "loc": [0, 64, 0], "material": "minecraft:twilightforest_travellers_boots", "amount": 1,
                  "meta": {"Damage": 3, "unhandled": unhandled, "custom": {"dd.rsf": {"$nbt": "5"}}, "slot": 0}}]
        cmds = self.run_commands(items, "--namespaces", "twilightforest,aether", "--overwrite")
        self.assertIn("twilightforest:travellers_boots[damage=3,custom_data={\"dd.rsf\":5},twilightforest:straight_ahead_multiplier=1.4d]", cmds[0])
        self.assertNotIn("unless items", cmds[0])

    def test_give_mode(self):
        items = [{"rowid": 3, "loc": [0, 0, 0], "material": "minecraft:bow", "amount": 1,
                  "meta": {"enchants": {"minecraft:power": 3}, "slot": 4}}]
        self.assertEqual(self.run_commands(items, "--mode", "give", "--player", "Steve"),
                         ['give Steve minecraft:bow[enchantments={levels:{"minecraft:power":3}}] 1'])

    def test_attribute_modifiers(self):
        items = [{"rowid": 4, "loc": [1, 2, 3], "material": "minecraft:aether_valkyrie_helmet", "amount": 1,
                  "meta": {"slot": 3, "modifiers": [{"minecraft:generic.armor": {"amount": 4.5, "slot": "head", "operation": 0, "key": "minecraft:armor.helmet"}}]}}]
        cmds = self.run_commands(items, "--namespaces", "aether")
        self.assertIn('aether:valkyrie_helmet[attribute_modifiers={modifiers:[{type:"minecraft:generic.armor",id:"minecraft:armor.helmet",amount:4.5d,operation:"add_value",slot:"head"}]}]', cmds[0])


if __name__ == "__main__":
    unittest.main()
