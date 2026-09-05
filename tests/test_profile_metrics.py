import json
from pathlib import Path
import unittest

from scripts.profile_metrics import overall_rank, trophy_rank, trophy_scores


class ProfileMetricsTests(unittest.TestCase):
    def test_overall_rank_reacts_to_private_activity(self):
        public = {"commits": 500, "prs": 20, "issues": 3, "reviews": 0, "stars": 5, "followers": 8}
        combined = {**public, "commits": 5000, "prs": 600, "issues": 40}
        self.assertEqual(overall_rank(public)[0], "C+")
        self.assertEqual(overall_rank(combined)[0], "B+")
        self.assertGreater(overall_rank(combined)[1], overall_rank(public)[1])
        self.assertEqual(overall_rank(dict.fromkeys(public, 0)), ("C", 0))

    def test_rank_matches_github_stats_extended_reference_values(self):
        # Lifetime-commit reference outputs from GitHub Stats Extended at 802f854.
        cases = [
            (1000, 50, 25, 10, 50, 10, "B+", 46.09375),
            (2000, 100, 50, 20, 200, 40, "A", 20.841471354166664),
            (4000, 200, 100, 40, 800, 160, "A+", 5.575988339442828),
            (5200, 1500, 4500, 1000, 600000, 50000, "S", 0.4578556547153667),
        ]
        for commits, prs, issues, reviews, stars, followers, expected_level, percentile in cases:
            with self.subTest(level=expected_level):
                level, progress = overall_rank({"commits": commits, "prs": prs, "issues": issues,
                                                "reviews": reviews, "stars": stars, "followers": followers})
                self.assertEqual(level, expected_level)
                self.assertAlmostEqual((1 - progress) * 100, percentile, places=10)

    def test_trophy_thresholds_switch_rank_art_at_the_boundary(self):
        rules = json.loads((Path(__file__).resolve().parents[1] / "assets/trophy-art.json").read_text())["rules"]
        self.assertEqual(trophy_rank(3999, rules["Commits"])[:2], ("SS", "Deep Committer"))
        self.assertEqual(trophy_rank(4000, rules["Commits"]), ("SSS", "God Committer", 1))
        self.assertEqual(trophy_rank(499, rules["PullRequest"])[0], "S")
        self.assertEqual(trophy_rank(500, rules["PullRequest"])[0], "SS")
        self.assertEqual(trophy_rank(0, rules["Stars"])[0], "?")

    def test_trophies_share_stats_counts_and_respect_account_anniversary(self):
        snapshot = {"created": "2018-03-15", "as_of": "2028-03-14", "language_count": 20,
                    "stats": {"commits": 5000, "prs": 600, "followers": 8, "repositories": 60, "stars": 5}}
        scores = trophy_scores(snapshot)
        self.assertEqual(scores["Commits"], snapshot["stats"]["commits"])
        self.assertEqual(scores["PullRequest"], snapshot["stats"]["prs"])
        self.assertEqual(scores["LongTimeUser"], 9)
        snapshot["as_of"] = "2028-03-15"
        self.assertEqual(trophy_scores(snapshot)["LongTimeUser"], 10)
        self.assertNotIn("Issues", scores)
        self.assertNotIn("Reviews", scores)


if __name__ == "__main__":
    unittest.main()
