"""Fetch language shares without persisting private repository details or byte totals."""

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES_QUERY = """
query($login: String!, $after: String) {
  viewer { login }
  user(login: $login) {
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, isFork: false) {
      nodes {
        id
        isPrivate
        languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
          pageInfo { hasNextPage endCursor }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
LANGUAGES_QUERY = """
query($id: ID!, $after: String!) {
  node(id: $id) {
    ... on Repository {
      languages(first: 100, after: $after, orderBy: {field: SIZE, direction: DESC}) {
        edges { size node { name color } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class FetchError(Exception):
    """A safe error message, without API response bodies or credentials."""


def github_request(query: str, variables: dict, *, use_gh: bool = False,
                   token_name: str = "PROFILE_STATS_TOKEN") -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    try:
        if use_gh:
            result = subprocess.run(
                ["gh", "api", "graphql", "--hostname", "github.com", "--input", "-"],
                input=payload, capture_output=True, timeout=60, check=False,
            )
            if result.returncode:
                raise FetchError("GitHub CLI request failed. Check login, token access, and rate limits.")
            response = json.loads(result.stdout)
        else:
            token = os.environ.get(token_name, "").strip()
            if not token:
                raise FetchError(f"Set the {token_name} Actions secret with the required read access.")
            request = Request(
                "https://api.github.com/graphql", data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "stanekdo-profile-cards"},
            )
            with urlopen(request, timeout=60) as result:
                response = json.load(result)
    except HTTPError as error:
        raise FetchError(f"GitHub returned HTTP {error.code}. Check token access and rate limits.") from None
    except (URLError, TimeoutError, subprocess.TimeoutExpired, OSError, ValueError):
        raise FetchError("GitHub request failed. Cached profile data was kept.") from None
    # GraphQL can return partial data together with errors. Never publish partial totals.
    if not isinstance(response, dict) or response.get("errors") or not isinstance(response.get("data"), dict):
        raise FetchError("GitHub returned incomplete data. Check token access and retry.")
    return response["data"]


def next_cursor(connection: dict, seen: set[str]) -> str | None:
    info = connection["pageInfo"]
    if not isinstance(info["hasNextPage"], bool):
        raise FetchError("GitHub returned invalid pagination data.")
    if not info["hasNextPage"]:
        return None
    cursor = info["endCursor"]
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise FetchError("GitHub returned an invalid pagination cursor.")
    seen.add(cursor)
    return cursor


def aggregate_languages(username: str, request: Callable[[str, dict], dict]) -> dict:
    totals: dict[str, int] = defaultdict(int)
    colors: dict[str, str] = {}
    after = None
    seen_pages: set[str] = set()
    seen_repositories: set[str] = set()
    private_visible = False
    try:
        while True:
            data = request(REPOSITORIES_QUERY, {"login": username, "after": after})
            if data["viewer"]["login"].casefold() != username.casefold():
                raise FetchError("The token must belong to the profile owner.")
            repositories = data["user"]["repositories"]
            for repository in repositories["nodes"]:
                repository_id = repository["id"]
                if repository_id in seen_repositories:
                    raise FetchError("GitHub returned duplicate repositories. Retry the refresh.")
                seen_repositories.add(repository_id)
                if not isinstance(repository["isPrivate"], bool):
                    raise FetchError("GitHub returned invalid repository visibility data.")
                private_visible |= repository["isPrivate"]
                languages = repository["languages"]
                seen_language_pages: set[str] = set()
                while True:
                    for edge in languages["edges"]:
                        name, size = edge["node"]["name"], edge["size"]
                        if not isinstance(name, str) or not name or type(size) is not int or size < 0:
                            raise FetchError("GitHub returned an invalid language size.")
                        color = edge["node"]["color"] or "#8b949e"
                        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                            raise FetchError("GitHub returned an invalid language color.")
                        totals[name] += size
                        colors[name] = color
                    language_after = next_cursor(languages, seen_language_pages)
                    if language_after is None:
                        break
                    languages = request(LANGUAGES_QUERY, {"id": repository_id, "after": language_after})["node"]["languages"]
            after = next_cursor(repositories, seen_pages)
            if after is None:
                break
    except (KeyError, TypeError, AttributeError):
        raise FetchError("GitHub returned incomplete language data. Cached totals were kept.") from None
    if not private_visible:
        raise FetchError("No private repositories are visible to this token. Check its repository access.")
    if not any(totals.values()):
        raise FetchError("No language bytes were returned. Cached totals were kept.")
    largest = sorted(((name, size) for name, size in totals.items() if size), key=lambda item: (-item[1], item[0]))[:8]
    total = sum(size for _, size in largest)
    return {
        "scope": "Owned, non-forked public and private repositories accessible to the token",
        "language_count": len([size for size in totals.values() if size]),
        "languages": [
            {"name": name, "percentage": round(size / total * 100, 6), "color": colors[name]}
            for name, size in largest
        ],
    }
