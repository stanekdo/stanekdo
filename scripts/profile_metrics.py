"""Rank and trophy calculations for aggregate GitHub data."""

from datetime import date


def overall_rank(stats: dict) -> tuple[str, float]:
    # GitHub Stats Extended formula at 802f854, using its lifetime commit median.
    # Source and MIT license: docs/profile.md and docs/licenses/github-stats-extended.txt.
    weighted = sum(weight * (1 - 2 ** (-stats[key] / median)) for key, median, weight in
                   (("commits", 1000, 2), ("prs", 50, 3), ("issues", 25, 1), ("reviews", 2, 1)))
    weighted += 4 * stats["stars"] / (50 + stats["stars"])
    weighted += stats["followers"] / (10 + stats["followers"])
    progress = weighted / 12
    for threshold, level in ((1, "S"), (12.5, "A+"), (25, "A"), (37.5, "A-"),
                             (50, "B+"), (62.5, "B"), (75, "B-"), (87.5, "C+"), (100, "C")):
        if (1 - progress) * 100 <= threshold:
            return level, progress
    raise ValueError("Invalid rank inputs")


def trophy_scores(snapshot: dict) -> dict[str, int]:
    stats = snapshot["stats"]
    created, today = date.fromisoformat(snapshot["created"]), date.fromisoformat(snapshot["as_of"])
    age = today.year - created.year - ((today.month, today.day) < (created.month, created.day))
    return {
        "MultiLanguage": snapshot["language_count"], "LongTimeUser": age,
        "Commits": stats["commits"], "Experience": (today - created).days // 100,
        "PullRequest": stats["prs"], "Followers": stats["followers"],
        "Repositories": stats["repositories"], "Stars": stats["stars"],
    }


def trophy_rank(score: int, rules: list[dict]) -> tuple[str, str, float]:
    for index, rule in enumerate(rules):
        if score >= rule["minimum"]:
            progress = 1 if index == 0 else (score - rule["minimum"]) / (rules[index - 1]["minimum"] - rule["minimum"])
            return rule["rank"], rule["title"], progress
    return "?", "Unknown", 0
