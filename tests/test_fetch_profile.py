from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from scripts import fetch_profile as fetch
from scripts.fetch_languages import FetchError, REPOSITORIES_QUERY as LANGUAGES_QUERY


def connection(nodes, cursor=None, **fields):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}, **fields}


def repository(identifier, private, stars):
    return {"id": identifier, "isPrivate": private, "stargazerCount": stars}


def fixture(query, variables):
    if query == fetch.ACCOUNT_QUERY:
        return {"viewer": {"login": "example-user"}, "user": {
            "createdAt": "2025-12-30T12:00:00Z", "followers": {"totalCount": 8},
            "pullRequests": {"totalCount": 19}, "issues": {"totalCount": 7}}}
    if query == fetch.REPOSITORIES_QUERY:
        if variables["after"] is None:
            repos = connection([repository("private-id", True, 3)], "next-page")
        else:
            repos = connection([repository("public-id", False, 5)])
        return {"user": {"repositories": repos}}
    if query == fetch.HISTORY_QUERY:
        start, end = date.fromisoformat(variables["from"][:10]), date.fromisoformat(variables["to"][:10])
        days = [{"date": (start + timedelta(days=i)).isoformat(), "contributionCount": i + 1}
                for i in range((end - start).days + 1)]
        return {"user": {"contributionsCollection": {
            "totalCommitContributions": 3 if start.year == 2025 else 4,
            "contributionCalendar": {"weeks": [{"contributionDays": days}]}}}}
    if query == fetch.RECENT_QUERY:
        groups = lambda *ids: [{"repository": {"id": identifier}} for identifier in ids]
        return {"user": {"contributionsCollection": {
            "totalPullRequestReviewContributions": 2,
            "totalRepositoriesWithContributedCommits": 2,
            "totalRepositoriesWithContributedIssues": 1,
            "totalRepositoriesWithContributedPullRequests": 1,
            "commitContributionsByRepository": groups("private-id", "public-id"),
            "issueContributionsByRepository": groups("private-id"),
            "pullRequestContributionsByRepository": groups("third-id"),
            "repositoryContributions": connection(groups("private-id"), "created-next", totalCount=2),
        }}}
    if query == fetch.CREATED_QUERY:
        return {"user": {"contributionsCollection": {"repositoryContributions":
            connection([{"repository": {"id": "fourth-id"}}])}}}
    if query == LANGUAGES_QUERY:
        languages = {"edges": [{"size": 300, "node": {"name": "Python", "color": "#3572A5"}}],
                     "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return {"viewer": {"login": "example-user"}, "user": {"repositories": connection([
            {"id": "private-id", "isPrivate": True, "languages": languages}])}}
    raise AssertionError("Unexpected query")


class FetchProfileTests(unittest.TestCase):
    today = date(2026, 1, 2)

    def test_public_private_and_year_pages_produce_aggregate_only_snapshot(self):
        calls = []
        def request(query, variables):
            calls.append((query, variables))
            return fixture(query, variables)
        result = fetch.aggregate_profile("example-user", request, self.today)
        self.assertEqual(result["stats"], {"followers": 8, "prs": 19, "issues": 7,
            "stars": 8, "repositories": 2, "commits": 7, "reviews": 2, "contributed_to": 4})
        self.assertEqual(result["streak"], {"total": 6, "current":
            {"weeks": 1, "start": "2025-12-28", "end": "2026-01-02"}})
        self.assertEqual([v["from"] for q, v in calls if q == fetch.HISTORY_QUERY],
                         ["2025-12-30T00:00:00Z", "2026-01-01T00:00:00Z"])
        saved = json.dumps(result)
        for detail in ("private-id", "public-id", "third-id", "fourth-id", "contributionDays", "stargazerCount", "size"):
            self.assertNotIn(detail, saved)

    def test_any_late_failure_keeps_previous_complete_snapshot(self):
        def request(query, variables):
            if query == LANGUAGES_QUERY:
                raise FetchError("Access failed")
            return fixture(query, variables)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profile.json"
            output.write_text("previous snapshot")
            with self.assertRaises(FetchError):
                fetch.refresh("example-user", output, request, self.today)
            self.assertEqual(output.read_text(), "previous snapshot")
            self.assertFalse(output.with_suffix(".json.tmp").exists())

    def test_incomplete_duplicate_or_invalid_counts_are_rejected(self):
        def changed(query_to_change, change):
            def request(query, variables):
                result = fixture(query, variables)
                if query == query_to_change:
                    change(result)
                return result
            return request
        cases = [
            changed(fetch.ACCOUNT_QUERY, lambda r: r["viewer"].update(login="another-user")),
            changed(fetch.ACCOUNT_QUERY, lambda r: r["user"]["issues"].update(totalCount=True)),
            changed(fetch.REPOSITORIES_QUERY, lambda r: r["user"]["repositories"]["nodes"][0].update(isPrivate=False)),
            changed(fetch.REPOSITORIES_QUERY, lambda r: r["user"]["repositories"]["nodes"][0].update(id="duplicate")),
            changed(fetch.RECENT_QUERY, lambda r: r["user"]["contributionsCollection"].update(totalRepositoriesWithContributedCommits=101)),
            changed(fetch.HISTORY_QUERY, lambda r: r["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"].pop()),
            changed(fetch.HISTORY_QUERY, lambda r: r["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"].append(
                deepcopy(r["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"][0]))),
        ]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(FetchError):
                fetch.aggregate_profile("example-user", request, self.today)

    def test_trailing_year_handles_leap_day(self):
        calls = []
        def request(query, variables):
            calls.append(variables)
            return fixture(query, variables)
        fetch.recent_activity("example-user", date(2024, 2, 29), request)
        self.assertEqual(calls[0]["from"], "2023-02-28T00:00:00Z")


class WeeklyStreakTests(unittest.TestCase):
    def test_sunday_boundary_and_empty_unfinished_week(self):
        days = {date(2025, 12, 27): 1, date(2025, 12, 28): 2, date(2026, 1, 3): 1}
        streak = fetch.weekly_streak(days, date(2026, 1, 4))
        self.assertEqual(streak["current"], {"weeks": 2, "start": "2025-12-21", "end": "2026-01-03"})
        self.assertEqual(streak["total"], 4)
        self.assertEqual(fetch.weekly_streak(days, date(2026, 1, 11))["current"]["weeks"], 0)

    def test_gap_starts_a_new_current_streak(self):
        days = {date(2026, 1, d): 1 for d in (1, 8, 22, 29)}
        result = fetch.weekly_streak(days, date(2026, 1, 30))
        self.assertEqual(result["current"], {"weeks": 2, "start": "2026-01-18", "end": "2026-01-30"})

    def test_no_activity_has_no_streak(self):
        result = fetch.weekly_streak({}, date(2026, 1, 1))
        self.assertEqual(result, {"total": 0, "current": {"weeks": 0, "start": None, "end": None}})


if __name__ == "__main__":
    unittest.main()
