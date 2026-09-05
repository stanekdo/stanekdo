"""Fetch aggregate public and private GitHub statistics without saving repository details."""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Callable

from .fetch_languages import FetchError, ROOT, aggregate_languages, github_request, next_cursor


ACCOUNT_QUERY = """
query($login: String!) {
  viewer { login }
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
  }
}
"""
REPOSITORIES_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(first: 100, after: $after, ownerAffiliations: OWNER) {
      nodes { id isPrivate stargazerCount }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
HISTORY_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  viewer { login }
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""
RECENT_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      restrictedContributionsCount
      totalPullRequestReviewContributions
      totalRepositoriesWithContributedCommits
      totalRepositoriesWithContributedIssues
      totalRepositoriesWithContributedPullRequests
      commitContributionsByRepository(maxRepositories: 100) { repository { id } }
      issueContributionsByRepository(maxRepositories: 100, excludeFirst: false, excludePopular: false) { repository { id } }
      pullRequestContributionsByRepository(maxRepositories: 100, excludeFirst: false, excludePopular: false) { repository { id } }
      repositoryContributions(first: 100, excludeFirst: false) {
        totalCount
        nodes { repository { id } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
CREATED_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!, $after: String!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      repositoryContributions(first: 100, after: $after, excludeFirst: false) {
        nodes { repository { id } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise FetchError("GitHub returned an invalid statistic count.")
    return value


def timestamp(day: date, *, end=False) -> str:
    return day.isoformat() + ("T23:59:59Z" if end else "T00:00:00Z")


def weekly_streak(days: dict[date, int], today: date) -> dict:
    """Sunday-start weeks; an unfinished empty week does not break the current streak."""
    def sunday(day):
        return day - timedelta(days=(day.weekday() + 1) % 7)

    active = sorted({sunday(day) for day, total in days.items() if total > 0 and day <= today})
    current: list[date] = []
    for week in active:
        if current and week - current[-1] != timedelta(days=7):
            current = []
        current.append(week)

    def summary(run):
        return {"weeks": len(run), "start": run[0].isoformat() if run else None,
                "end": min(run[-1] + timedelta(days=6), today).isoformat() if run else None}

    if current and sunday(today) - current[-1] > timedelta(days=7):
        current = []
    return {"total": sum(days.values()), "current": summary(current)}


def recent_activity(username: str, today: date, request: Callable[[str, dict], dict]) -> dict:
    try:
        start = today.replace(year=today.year - 1)
    except ValueError:  # February 29 has no counterpart in a non-leap year.
        start = today.replace(year=today.year - 1, day=28)
    variables = {"login": username, "from": timestamp(start), "to": timestamp(today, end=True)}
    collection = request(RECENT_QUERY, variables)["user"]["contributionsCollection"]
    if count(collection["restrictedContributionsCount"]):
        raise FetchError("Private contribution data is restricted. Check contribution token access.")
    identifiers = set()
    for prefix, suffix in (("commit", "Commits"), ("issue", "Issues"), ("pullRequest", "PullRequests")):
        groups = collection[prefix + "ContributionsByRepository"]
        expected = count(collection["totalRepositoriesWithContributed" + suffix])
        group_ids = {group["repository"]["id"] for group in groups}
        if len(group_ids) != expected:
            raise FetchError("GitHub did not return all contributed repositories. Cached totals were kept.")
        identifiers.update(group_ids)
    created = collection["repositoryContributions"]
    expected_created = count(created["totalCount"])
    created_ids = set()
    seen = set()
    while True:
        created_ids.update(item["repository"]["id"] for item in created["nodes"])
        after = next_cursor(created, seen)
        if after is None:
            break
        created = request(CREATED_QUERY, {**variables, "after": after})["user"]["contributionsCollection"]["repositoryContributions"]
    if len(created_ids) != expected_created:
        raise FetchError("GitHub did not return all created repositories. Cached totals were kept.")
    identifiers.update(created_ids)
    return {"contributed_to": len(identifiers), "reviews": count(collection["totalPullRequestReviewContributions"])}


def aggregate_profile(username: str, request: Callable[[str, dict], dict], today: date) -> dict:
    try:
        account = request(ACCOUNT_QUERY, {"login": username})
        if account["viewer"]["login"].casefold() != username.casefold():
            raise FetchError("The token must belong to the profile owner.")
        user = account["user"]
        created = date.fromisoformat(user["createdAt"][:10])
        if not date(2007, 1, 1) <= created <= today:
            raise FetchError("GitHub returned an invalid account creation date.")
        stats = {name: count(user[field]["totalCount"]) for name, field in
                 (("followers", "followers"), ("prs", "pullRequests"), ("issues", "issues"))}
        stats.update(stars=0, repositories=0, commits=0)
        after = None
        seen_pages, seen_repositories = set(), set()
        private_visible = False
        while True:
            repos = request(REPOSITORIES_QUERY, {"login": username, "after": after})["user"]["repositories"]
            for repo in repos["nodes"]:
                if not isinstance(repo["id"], str) or not repo["id"] or repo["id"] in seen_repositories:
                    raise FetchError("GitHub returned invalid or duplicate repositories.")
                seen_repositories.add(repo["id"])
                if type(repo["isPrivate"]) is not bool:
                    raise FetchError("GitHub returned invalid repository visibility data.")
                private_visible |= repo["isPrivate"]
                stats["stars"] += count(repo["stargazerCount"])
                stats["repositories"] += 1
            after = next_cursor(repos, seen_pages)
            if after is None:
                break
        if not private_visible:
            raise FetchError("No private repositories are visible to this token. Check its repository access.")

        days = {}
        for year in range(created.year, today.year + 1):
            start, end = max(created, date(year, 1, 1)), min(today, date(year, 12, 31))
            history = request(HISTORY_QUERY, {"login": username, "from": timestamp(start), "to": timestamp(end, end=True)})
            if history["viewer"]["login"].casefold() != username.casefold():
                raise FetchError("The contribution token must belong to the profile owner.")
            collection = history["user"]["contributionsCollection"]
            if count(collection["restrictedContributionsCount"]):
                raise FetchError("Private contribution data is restricted. Check contribution token access.")
            stats["commits"] += count(collection["totalCommitContributions"])
            year_days = {}
            for week in collection["contributionCalendar"]["weeks"]:
                for item in week["contributionDays"]:
                    day = date.fromisoformat(item["date"])
                    if start <= day <= end:
                        if day in year_days:
                            raise FetchError("GitHub returned duplicate calendar days.")
                        year_days[day] = count(item["contributionCount"])
            if len(year_days) != (end - start).days + 1:
                raise FetchError("GitHub returned an incomplete contribution calendar.")
            days.update(year_days)
        stats.update(recent_activity(username, today, request))
        languages = aggregate_languages(username, request)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise FetchError("GitHub returned incomplete profile data. Cached totals were kept.") from None
    return {
        "scope": "Public and private activity accessible to the profile owner's token",
        "as_of": today.isoformat(), "created": created.isoformat(),
        "stats": stats, "streak": weekly_streak(days, today),
        "language_count": languages["language_count"], "languages": languages["languages"],
    }


def refresh(username: str, output: Path, request: Callable[[str, dict], dict], today: date) -> None:
    content = json.dumps(aggregate_profile(username, request, today), indent=2, ensure_ascii=False) + "\n"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="stanekdo")
    parser.add_argument("--use-gh", action="store_true", help="Use GitHub CLI without exporting its login token.")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/profile.json")
    args = parser.parse_args()
    try:
        refresh(args.username, args.output,
                lambda query, variables: github_request(query, variables, use_gh=args.use_gh,
                    token_name="PROFILE_CONTRIBUTIONS_TOKEN" if query in (HISTORY_QUERY, RECENT_QUERY, CREATED_QUERY)
                    else "PROFILE_STATS_TOKEN"),
                datetime.now(timezone.utc).date())
    except FetchError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("Updated aggregate GitHub statistics, streaks, trophies, and languages, including private activity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
