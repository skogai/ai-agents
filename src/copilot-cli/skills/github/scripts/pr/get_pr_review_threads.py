#!/usr/bin/env python3
"""Get all review threads for a GitHub Pull Request.

Retrieves review threads with their resolution status, comments,
and thread IDs needed for resolve/reply operations.

Complements get_unresolved_review_threads by providing thread-level
context rather than just unresolved threads.

Exit codes follow ADR-035:
    0 - Success
    1 - Invalid parameters / logic error
    2 - Not found
    3 - External error (API failure)
    4 - Auth error
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
_workspace = os.environ.get("GITHUB_WORKSPACE")
if _plugin_root:
    _lib_dir = os.path.join(_plugin_root, "lib")
elif _workspace:
    _lib_dir = os.path.join(_workspace, ".claude", "lib")
else:
    _lib_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib")
    )
if not os.path.isdir(_lib_dir):
    print(f"Plugin lib directory not found: {_lib_dir}", file=sys.stderr)
    sys.exit(2)  # Config error per ADR-035

if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from github_core.api import (  # noqa: E402
    assert_gh_authenticated,
    error_and_exit,
    gh_graphql,
    resolve_repo_params,
)

# Page size for the reviewThreads connection. GitHub GraphQL caps connection
# pages at 100; we ask for 100 and iterate until pageInfo.hasNextPage is false.
# The PR #1887 retrospective records that the earlier first:100 single-page
# query masked 6+ unresolved threads twice in that cycle, because the script
# silently truncated on PRs whose thread count crossed the page boundary.
_THREADS_PAGE_SIZE = 100

# Safety bound: a PR with more than 5000 review threads is almost certainly
# either a runaway state or a query targeting the wrong PR. We exit the loop
# rather than spin forever; the cap is reported in the output so callers can
# see it tripped.
_MAX_THREAD_PAGES = 50

_THREADS_QUERY = """\
query($owner: String!, $repo: String!, $prNumber: Int!, $commentsLimit: Int!, $cursor: String) {
    repository(owner: $owner, name: $repo) {
        pullRequest(number: $prNumber) {
            reviewThreads(first: 100, after: $cursor) {
                totalCount
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    id
                    isResolved
                    isOutdated
                    path
                    line
                    startLine
                    diffSide
                    comments(first: $commentsLimit) {
                        totalCount
                        nodes {
                            id
                            databaseId
                            body
                            author { login }
                            createdAt
                            updatedAt
                        }
                    }
                }
            }
        }
    }
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Get all review threads for a GitHub PR.",
    )
    parser.add_argument("--owner", default="", help="Repository owner")
    parser.add_argument("--repo", default="", help="Repository name")
    parser.add_argument(
        "--pull-request", type=int, required=True, help="Pull request number",
    )
    parser.add_argument(
        "--unresolved-only", action="store_true",
        help="Return only unresolved threads",
    )
    parser.add_argument(
        "--include-comments", action="store_true",
        help="Include all comments in each thread (not just first)",
    )
    return parser


def _run_threads_query(
    owner: str, repo: str, pr: int, comments_limit: int,
    cursor: str | None = None,
) -> dict:
    """Execute the parameterized GraphQL query for one page of review threads.

    The `cursor` argument is the GraphQL cursor returned by the previous page's
    `pageInfo.endCursor`. On the first call it is None; the query treats a None
    `$cursor` as "from the start". Callers iterate via `_collect_all_threads`.
    """
    variables = {
        "owner": owner,
        "repo": repo,
        "prNumber": pr,
        "commentsLimit": comments_limit,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return gh_graphql(_THREADS_QUERY, variables)


def _collect_all_threads(
    owner: str, repo: str, pr: int, comments_limit: int,
) -> tuple[list[dict] | None, int]:
    """Page through all reviewThreads for a PR.

    Returns a tuple of (nodes, total_count). The nodes list aggregates every
    page until pageInfo.hasNextPage is false or _MAX_THREAD_PAGES is reached.
    Returns (None, 0) when the PR or its reviewThreads field is missing, so
    the caller can distinguish "no threads on a real PR" (empty list, total
    >=0) from "PR not found" (None nodes).

    Raises RuntimeError on transport failures, propagated from gh_graphql.
    The PR #1887 retrospective records that the prior first:100 single-page
    query reported "0 unresolved" twice while 6+ unresolved threads sat on
    the second page; this loop closes that pagination cliff.
    """
    aggregated: list[dict] = []
    total_count = 0
    cursor: str | None = None
    pages_seen = 0

    while pages_seen < _MAX_THREAD_PAGES:
        pages_seen += 1
        data = _run_threads_query(owner, repo, pr, comments_limit, cursor)

        review_threads = (
            data.get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads")
        )
        if review_threads is None:
            return None, 0

        page_nodes = review_threads.get("nodes")
        if page_nodes is None:
            return None, 0

        aggregated.extend(page_nodes)
        total_count = review_threads.get("totalCount", total_count)

        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    return aggregated, total_count


def _transform_thread(thread: dict, include_comments: bool) -> dict:
    """Transform a raw GraphQL thread node into the output format."""
    comments_nodes = thread.get("comments", {}).get("nodes", [])
    first = comments_nodes[0] if comments_nodes else None

    result: dict = {
        "thread_id": thread.get("id"),
        "is_resolved": thread.get("isResolved", False),
        "is_outdated": thread.get("isOutdated", False),
        "path": thread.get("path"),
        "line": thread.get("line"),
        "start_line": thread.get("startLine"),
        "diff_side": thread.get("diffSide"),
        "comment_count": thread.get("comments", {}).get("totalCount", 0),
        "first_comment_id": first.get("databaseId") if first else None,
        "first_comment_author": (
            first.get("author", {}).get("login") if first and first.get("author") else None
        ),
        "first_comment_body": first.get("body") if first else None,
        "first_comment_created_at": first.get("createdAt") if first else None,
        "comments": None,
    }

    if include_comments:
        result["comments"] = [
            {
                "id": c.get("databaseId"),
                "author": c.get("author", {}).get("login") if c.get("author") else None,
                "body": c.get("body"),
                "created_at": c.get("createdAt"),
            }
            for c in comments_nodes
        ]

    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    assert_gh_authenticated()
    resolved = resolve_repo_params(args.owner, args.repo)
    owner, repo = resolved.owner, resolved.repo
    pr = args.pull_request

    comments_limit = 50 if args.include_comments else 1

    try:
        threads, _total_count = _collect_all_threads(owner, repo, pr, comments_limit)
    except RuntimeError as exc:
        msg = str(exc)
        if "Could not resolve" in msg:
            error_and_exit(f"PR #{pr} not found in {owner}/{repo}", 2)
        error_and_exit(f"Failed to query review threads: {msg}", 3)

    if threads is None:
        error_and_exit(f"PR #{pr} not found or has no review threads", 2)

    if args.unresolved_only:
        threads = [t for t in threads if not t.get("isResolved", True)]

    transformed = [_transform_thread(t, args.include_comments) for t in threads]

    total = len(threads)
    unresolved = sum(1 for t in threads if not t.get("isResolved", True))

    output = {
        "success": True,
        "pull_request": pr,
        "owner": owner,
        "repo": repo,
        "total_threads": total,
        "resolved_count": total - unresolved,
        "unresolved_count": unresolved,
        "threads": transformed,
    }

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
