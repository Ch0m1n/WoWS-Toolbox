from pathlib import Path
from types import SimpleNamespace
import copy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_modules import module_options, resolve_options, loadout_key, validate_module_id
from workflow import request_identity


def fixture():
    # Keys deliberately sort opposite to research order.
    def upgrade(kind, components, prev=""):
        return dict(ucType=kind, components=components, prev=prev)
    return {
        "ShipUpgradeInfo": {
            "Z_Hull": upgrade("_Hull", {"hull": ["A_Hull"], "artillery": ["A1", "A2"], "torpedoes": ["T1", "T2"]}),
            "A_Hull": upgrade("_Hull", {"hull": ["B_Hull"], "artillery": ["B1", "B2"], "torpedoes": ["T2"]}, "Z_Hull"),
            "Z_Gun": upgrade("_Artillery", {"artillery": ["A1", "B1"]}),
            "A_Gun": upgrade("_Artillery", {"artillery": ["A2", "B2"]}, "Z_Gun"),
            "Z_Torp": upgrade("_Torpedoes", {"torpedoes": ["T1"]}),
            "A_Torp": upgrade("_Torpedoes", {"torpedoes": ["T2"]}, "Z_Torp"),
        },
        "A_Hull": {"model": "content/gameplay/ship/Stock/Stock.model"},
        "B_Hull": {"model": "content/gameplay/ship/Top/Top.model"},
    }


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.options = module_options(self.data)

    def test_default_follows_research_dependencies(self):
        result = resolve_options(self.options)
        self.assertEqual(result["hull_upgrade"], "A_Hull")
        self.assertEqual(result["artillery_component"], "B2")
        self.assertEqual(result["torpedo_component"], "T2")
        self.assertIn("/Top/", result["model_path"])

    def test_stock_hull_with_researched_guns_uses_stock_hull_mounts(self):
        result = resolve_options(self.options, "Z_Hull", "A_Gun")
        self.assertEqual(result["artillery_component"], "A2")
        self.assertIn("/Stock/", result["model_path"])

    def test_top_hull_stock_guns(self):
        self.assertEqual(resolve_options(self.options, "top", "Z_Gun")["artillery_component"], "B1")

    def test_invalid_cross_hull_torpedo_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incompatible"):
            resolve_options(self.options, "A_Hull", "top", "Z_Torp")

    def test_missing_and_foreign_ids_are_rejected(self):
        for choices in (("foreign", "top", "top"), ("top", "foreign", "top")):
            with self.assertRaises(ValueError):
                resolve_options(self.options, *choices)

    def test_no_torpedoes_is_valid(self):
        for upgrade in self.data["ShipUpgradeInfo"].values():
            upgrade["components"].pop("torpedoes", None)
        self.assertEqual(resolve_options(module_options(self.data))["torpedo_component"], "")

    def test_no_research_data(self):
        self.assertEqual(resolve_options(module_options({}))["hull_upgrade"], "")

    def test_ambiguous_mapping_fails_instead_of_combining_mounts(self):
        self.data["ShipUpgradeInfo"]["A_Gun"]["components"]["artillery"].append("A1")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            module_options(self.data)

    def test_all_three_choices_separate_cache_and_request_identity(self):
        args = SimpleNamespace(hull_upgrade="top", artillery_upgrade="top", torpedo_upgrade="top")
        keys = {loadout_key(args)}
        identities = {request_identity(args, {"build": 123})}
        for field in ("hull_upgrade", "artillery_upgrade", "torpedo_upgrade"):
            changed = copy.copy(args)
            setattr(changed, field, "stock")
            keys.add(loadout_key(changed))
            identities.add(request_identity(changed, {"build": 123}))
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(identities), 4)

    def test_unsafe_ids(self):
        for value in ("../bad", "x/y", "x\\y", "x" * 201):
            with self.assertRaises(ValueError):
                validate_module_id(value)


if __name__ == "__main__":
    unittest.main()
