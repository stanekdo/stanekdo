from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from scripts.fetch_languages import FetchError, aggregate_languages, github_request


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def language_edges(values, cursor=None):
    return {
        "edges": [{"size": size, "node": {"name": name, "color": "#123456"}} for name, size in values],
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
    }


def repository(identifier, private, values, cursor=None):
    return {"id": identifier, "isPrivate": private, "languages": language_edges(values, cursor)}


def response(repositories, cursor=None, owner="example-user"):
    return {"viewer": {"login": owner}, "user": {"repositories": connection(repositories, cursor)}}


class FetchLanguagesTests(unittest.TestCase):
    def test_combines_private_and_public_bytes_across_both_pagination_levels(self):
        calls = []
        responses = [
            response([repository("private-id", True, [("Python", 100)], "languages-next")], "repos-next"),
            {"node": {"languages": language_edges([("Rust", 20)])}},
            response([repository("public-id", False, [("Python", 200), ("TypeScript", 50)])]),
        ]
        def request(query, variables):
            calls.append(variables)
            return responses.pop(0)
        result = aggregate_languages("example-user", request)
        self.assertEqual({x["name"]: x["percentage"] for x in result["languages"]}, {"Python": 81.081081, "TypeScript": 13.513514, "Rust": 5.405405})
        self.assertEqual(calls, [{"login": "example-user", "after": None}, {"id": "private-id", "after": "languages-next"}, {"login": "example-user", "after": "repos-next"}])
        self.assertNotIn("private-id", json.dumps(result))
        self.assertNotIn("public-id", json.dumps(result))
        self.assertNotIn("bytes", json.dumps(result))

    def test_only_top_eight_languages_are_normalized_for_public_output(self):
        languages = [(f"Language {i}", i) for i in range(1, 11)]
        result = aggregate_languages("example-user", lambda *_: response([repository("private", True, languages)]))
        self.assertEqual(len(result["languages"]), 8)
        self.assertEqual(result["languages"][0]["name"], "Language 10")
        self.assertAlmostEqual(result["languages"][0]["percentage"], 10 / 52 * 100, places=5)
        self.assertAlmostEqual(sum(x["percentage"] for x in result["languages"]), 100, places=5)

    def test_no_private_access_is_rejected(self):
        with self.assertRaisesRegex(FetchError, "No private repositories"):
            aggregate_languages("example-user", lambda *_: response([repository("public", False, [("Python", 10)])]))

    def test_failed_later_page_never_returns_partial_totals(self):
        responses = [response([repository("private", True, [("Python", 10)])], "next")]
        def request(*_):
            if responses:
                return responses.pop()
            raise FetchError("API failed")
        with self.assertRaises(FetchError):
            aggregate_languages("example-user", request)

    def test_duplicate_repository_cannot_inflate_totals(self):
        item = repository("duplicate", True, [("Python", 10)])
        with self.assertRaisesRegex(FetchError, "duplicate repositories"):
            aggregate_languages("example-user", lambda *_: response([item, item]))

    def test_repeated_cursor_stops_pagination(self):
        responses = [response([], "repeated"), response([], "repeated")]
        with self.assertRaisesRegex(FetchError, "pagination cursor"):
            aggregate_languages("example-user", lambda *_: responses.pop(0))

    def test_wrong_owner_is_rejected(self):
        with self.assertRaisesRegex(FetchError, "profile owner"):
            aggregate_languages("example-user", lambda *_: response([], owner="someone-else"))

    def test_empty_or_invalid_data_is_rejected(self):
        valid = response([repository("private", True, [("Python", 10)])])
        cases = [response([repository("empty", True, [])]), {"viewer": {"login": "example-user"}}]
        for size in (-1, True, "10"):
            invalid = deepcopy(valid)
            invalid["user"]["repositories"]["nodes"][0]["languages"]["edges"][0]["size"] = size
            cases.append(invalid)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(FetchError):
                aggregate_languages("example-user", lambda *_: data)

    def test_missing_secret_has_a_clear_error(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(FetchError, "PROFILE_STATS_TOKEN"):
            github_request("query", {})

    def test_graphql_partial_response_is_rejected_without_logging_private_details(self):
        class Result:
            returncode = 0
            stdout = json.dumps({"data": {"partial": True}, "errors": [{"message": "private-repository-name"}]}).encode()
        with patch("scripts.fetch_languages.subprocess.run", return_value=Result()):
            with self.assertRaises(FetchError) as error:
                github_request("query", {}, use_gh=True)
        self.assertNotIn("private-repository-name", str(error.exception))


if __name__ == "__main__":
    unittest.main()
