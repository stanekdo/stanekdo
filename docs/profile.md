# Profile cards

## Refresh

1. Create a fine-grained GitHub token for the profile owner with access to all owned repositories.
2. Grant read-only Contents, Issues, Pull requests, and Metadata permissions.
3. Store it in the repository Actions secret `PROFILE_STATS_TOKEN`.
4. Create a classic token for the same owner with only the `read:user` scope.
5. Store it in the repository Actions secret `PROFILE_CONTRIBUTIONS_TOKEN`.
6. Run the `Update profile cards` workflow.

The workflow runs every Monday at 04:00 UTC. Failed or incomplete fetches keep the previous snapshot.

For a local refresh, sign in to GitHub CLI as the profile owner, then run:

```sh
python -m scripts.fetch_profile --use-gh
python -m scripts.render_profile
python -m unittest discover -s tests
```

## Counting limits

- Private coverage depends on the credential's repository access. Contribution totals can include anonymous private activity; commits follow [GitHub contribution rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference).
- Streak weeks start on Sunday. An unfinished week with no activity does not break the current streak.
- Stars and repository totals cover owned repositories, including forks. Languages exclude forks and measure repository code bytes, normalized across the eight largest languages. MultiLanguage counts all returned languages.
- Contributed to counts distinct repositories with commits, issues, pull requests, or repository creation during the past year.
- Rank uses lifetime commits and reviews from the past year. Experience counts blocks of 100 days; LongTimeUser counts completed years.

## Sources

The rank formula comes from [GitHub Stats Extended](https://github.com/stats-organization/github-stats-extended/blob/802f85426692ce9e2f98c969e0fa9d1ba3bd95b2/packages/core/src/calculateRank.ts). Trophy rules and artwork come from [GitHub Profile Trophy](https://github.com/ryo-ma/github-profile-trophy/tree/e3c89df995e92e67cdd4b2acaab9d974583dc1f7). Their MIT licenses are in [licenses/](licenses/).
