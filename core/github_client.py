"""
core/github_client.py — GitHub API integration for AutoJob.

Fetches the candidate's public (and private, with token) repositories and
builds two things used during CV generation:

  1. A dict mapping repo/project name → GitHub URL
     {"WordWar": "https://github.com/joe/wordwar", ...}

  2. A rich project-context block injected into the Groq CV prompt so the
     model can write concrete, accurate bullets (languages, description,
     topics, stars) instead of guessing.

Usage
-----
  from core.github_client import GitHubClient
  gh = GitHubClient(token="ghp_...")
  urls    = gh.project_url_map(candidate_projects)   # {"PulseQuiz": "https://..."}
  context = gh.project_context_block(candidate_projects)  # prompt text
"""

import logging
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    """Lightweight GitHub REST v3 client — no extra dependencies."""

    def __init__(self, token: str = "", username: str = ""):
        self.token    = token.strip()
        self.username = username.strip().lstrip("https://github.com/").strip("/").split("/")[0] if username else ""
        self._repos: Optional[list[dict]] = None  # cached

    # ──────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────

    def project_url_map(self, candidate_projects: list[str]) -> dict[str, str]:
        """
        Return a dict: {project_name: github_url} for every project name
        that fuzzy-matches a repo in the candidate's GitHub account.

        Matching is case-insensitive and ignores hyphens/underscores/spaces.
        """
        repos = self._fetch_repos()
        if not repos:
            return {}

        result: dict[str, str] = {}
        for proj_name in candidate_projects:
            repo = self._best_match(proj_name, repos)
            if repo:
                result[proj_name] = repo["html_url"]
                logger.info(f"[GitHub] Matched '{proj_name}' → {repo['html_url']}")
            else:
                logger.debug(f"[GitHub] No match for project '{proj_name}'")
        return result

    def project_context_block(self, candidate_projects: list[str]) -> str:
        """
        Build a rich text block for injection into the Groq CV prompt.
        Includes: URL, description, languages, topics, stars.
        Returns empty string when GitHub is unavailable.
        """
        repos = self._fetch_repos()
        if not repos:
            return ""

        lines: list[str] = ["GITHUB PROJECT DETAILS (use for accurate bullets and URLs):"]
        matched = 0
        for proj_name in candidate_projects:
            repo = self._best_match(proj_name, repos)
            if not repo:
                continue
            matched += 1
            lines.append(f"\n• {proj_name}")
            lines.append(f"  URL:         {repo['html_url']}")
            if repo.get("description"):
                lines.append(f"  Description: {repo['description']}")
            if repo.get("language"):
                lines.append(f"  Language:    {repo['language']}")
            if repo.get("topics"):
                lines.append(f"  Topics:      {', '.join(repo['topics'])}")
            if repo.get("stargazers_count"):
                lines.append(f"  Stars:       {repo['stargazers_count']}")
            if repo.get("pushed_at"):
                lines.append(f"  Last push:   {repo['pushed_at'][:10]}")

        if matched == 0:
            return ""
        return "\n".join(lines)

    def all_repos_summary(self) -> list[dict]:
        """
        Return a simplified list of all repos — used to populate the
        CANDIDATE_PROJECT_URLS field in settings automatically.
        Each item: {name, html_url, description, language, topics}
        """
        repos = self._fetch_repos()
        if not repos:
            return []
        return [
            {
                "name":        r.get("name", ""),
                "html_url":    r.get("html_url", ""),
                "description": r.get("description") or "",
                "language":    r.get("language") or "",
                "topics":      r.get("topics") or [],
            }
            for r in repos
            if not r.get("fork", False)  # skip forks by default
        ]

    # ──────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────

    def _fetch_repos(self) -> list[dict]:
        """Fetch all repos for the authenticated user (or public repos by username)."""
        if self._repos is not None:
            return self._repos

        if not self.token and not self.username:
            logger.debug("[GitHub] No token or username configured — skipping")
            self._repos = []
            return []

        repos: list[dict] = []
        page = 1
        while True:
            if self.token:
                # Authenticated: gets private repos too, no username needed
                url = f"{GITHUB_API}/user/repos?per_page=100&page={page}&sort=pushed"
            else:
                # Public only
                url = f"{GITHUB_API}/users/{self.username}/repos?per_page=100&page={page}&sort=pushed"

            batch = self._get(url)
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(0.2)  # be polite

        # Fetch topics for each repo (topics need a separate Accept header)
        for repo in repos:
            owner = repo.get("owner", {}).get("login", "")
            name  = repo.get("name", "")
            if owner and name:
                topics_data = self._get(
                    f"{GITHUB_API}/repos/{owner}/{name}/topics",
                    extra_headers={"Accept": "application/vnd.github.mercy-preview+json"},
                )
                if topics_data and "names" in topics_data:
                    repo["topics"] = topics_data["names"]
                else:
                    repo.setdefault("topics", [])

        logger.info(f"[GitHub] Fetched {len(repos)} repos")
        self._repos = repos
        return repos

    def _get(self, url: str, extra_headers: Optional[dict] = None) -> Optional[any]:
        headers = {
            "Accept":     "application/vnd.github+json",
            "User-Agent": "AutoJob/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)

        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code == 401:
                logger.warning("[GitHub] Token is invalid or expired (401)")
            elif e.code == 403:
                logger.warning("[GitHub] Rate-limited or permission denied (403)")
            elif e.code == 404:
                logger.warning(f"[GitHub] Not found: {url}")
            else:
                logger.warning(f"[GitHub] HTTP {e.code}: {url}")
        except URLError as e:
            logger.warning(f"[GitHub] Network error: {e.reason}")
        except Exception as e:
            logger.warning(f"[GitHub] Unexpected error: {e}")
        return None

    @staticmethod
    def _normalise(name: str) -> str:
        """Lowercase, strip hyphens/underscores/spaces for fuzzy matching."""
        return name.lower().replace("-", "").replace("_", "").replace(" ", "")

    def _best_match(self, proj_name: str, repos: list[dict]) -> Optional[dict]:
        """Find the repo whose name best matches proj_name."""
        needle = self._normalise(proj_name)
        # Exact normalised match
        for r in repos:
            if self._normalise(r.get("name", "")) == needle:
                return r
        # Substring match (proj_name contained in repo name or vice versa)
        for r in repos:
            rn = self._normalise(r.get("name", ""))
            if needle in rn or rn in needle:
                return r
        return None
