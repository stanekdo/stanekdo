from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.render_profile import NS, ROOT, RenderError, Svg, language_card, load_profile, render, render_files, streak_card, texts


class RenderProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile(ROOT / "assets")

    def test_theme_files_preserve_stats_and_trophy_shapes(self):
        source_paths = []
        for trophy in self.profile.trophies:
            card = ET.fromstring(f'<svg xmlns="{NS[1:-1]}">{trophy.artwork}</svg>')
            source_paths.extend(e.attrib["d"] for e in card.iter(NS + "path"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            render_files(ROOT / "assets", output)
            self.assertEqual({path.name for path in output.iterdir()}, {"profile-dark.svg", "profile-light.svg"})
            for theme in ("dark", "light"):
                with self.subTest(theme=theme):
                    root = ET.parse(output / f"profile-{theme}.svg").getroot()
                    visible = " ".join(texts(root))
                    for value in [self.profile.streak[i] for i in (0, 2, 4, 5)]:
                        self.assertIn(value, visible)
                    for label, value in self.profile.stats:
                        self.assertIn(label.rstrip(":"), visible)
                        self.assertIn(value, visible)
                    self.assertIn(self.profile.rank, visible)
                    for trophy in self.profile.trophies:
                        self.assertIn(trophy.name, visible)
                        self.assertIn(trophy.points, visible)
                        if trophy.rank != "?":
                            self.assertIn(trophy.title, visible)
                    paths = [e.attrib["d"] for e in root.iter(NS + "path")]
                    for shape in source_paths:
                        self.assertIn(shape, paths)
                    self.assertNotIn("Code. Keep going", visible)
                    self.assertNotIn("Unranked", visible)
                    self.assertNotIn("Reviews", texts(root))
                    self.assertNotIn("Issues", texts(root))
                    ids = [e.attrib["id"] for e in root.iter() if "id" in e.attrib]
                    self.assertEqual(len(ids), len(set(ids)))
                    references = re.findall(r"url\(#([^)]+)\)", ET.tostring(root, encoding="unicode"))
                    self.assertTrue(set(references) <= set(ids))
                    self.assertFalse(list(root.iter(NS + "style")))

    def test_language_bar_uses_byte_weights_and_escapes_names(self):
        profile = deepcopy(self.profile)
        profile.languages = [{"name": "A & B", "percentage": 75, "color": "#123456"}, {"name": "C++", "percentage": 25, "color": "#654321"}]
        root = ET.fromstring(f'<svg xmlns="{NS[1:-1]}">{language_card(Svg("dark"), profile)}</svg>')
        labels = texts(root)
        self.assertIn("A & B", labels)
        self.assertIn("75.00%", labels)
        self.assertIn("25.00%", labels)
        bars = [e for e in root.iter(NS + "rect") if e.attrib.get("y") == "56"]
        self.assertAlmostEqual(float(bars[0].attrib["width"]), 214.5)
        self.assertAlmostEqual(sum(float(e.attrib["width"]) for e in bars), 286)

    def test_new_ranked_trophies_expand_layout_instead_of_clipping(self):
        profile = deepcopy(self.profile)
        before = int(ET.fromstring(render(profile, "dark")).attrib["height"])
        profile.trophies.append(deepcopy(profile.trophies[-1]))
        after = ET.fromstring(render(profile, "dark"))
        self.assertGreater(int(after.attrib["height"]), before)
        self.assertIn(profile.trophies[-1].title, " ".join(texts(after)))

    def test_invalid_source_does_not_replace_existing_generated_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            for name in ("profile.json", "trophy-art.json"):
                shutil.copy2(ROOT / "assets" / name, assets / name)
            output = assets / "profile-dark.svg"
            output.write_text("previous card")
            snapshot = json.loads((assets / "profile.json").read_text())
            snapshot["stats"]["commits"] = -1
            (assets / "profile.json").write_text(json.dumps(snapshot))
            with self.assertRaises(RenderError):
                render_files(assets, assets)
            self.assertEqual(output.read_text(), "previous card")

    def test_streak_has_one_count_without_floating_longest_statistic(self):
        root = ET.fromstring(f'<svg xmlns="{NS[1:-1]}">{streak_card(Svg("dark"), self.profile)}</svg>')
        labels = texts(root)
        self.assertEqual(labels.count(self.profile.streak[5]), 1)
        self.assertNotIn("Longest week streak", labels)

    def test_readme_uses_theme_images_and_places_views_last(self):
        readme = (ROOT / "README.md").read_text()
        self.assertGreater(readme.index("komarev.com"), readme.index("</picture>"))
        self.assertIn('<p align="center">', readme)
        self.assertIn("(prefers-color-scheme: dark)", readme)
        self.assertNotIn("max-width:", readme)
        self.assertNotIn("profile-mobile-", readme)
        self.assertIn('width="780"', readme)
        for path in re.findall(r'(?:src|srcset)="\./([^\"]+)"', readme):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
