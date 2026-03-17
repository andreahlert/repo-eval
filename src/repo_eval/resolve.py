"""Resolve package name and metadata from PyPI or GitHub URL."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional


def parse_github_url(url: str) -> tuple[str, str] | None:
    url = url.rstrip("/").replace(".git", "")
    parts = url.split("/")
    for i, part in enumerate(parts):
        if part in ("github.com", "www.github.com") and i + 2 < len(parts):
            return parts[i + 1], parts[i + 2]
    return None


def is_github_url(target: str) -> bool:
    return "github.com" in target


def resolve_pypi_name_from_repo(repo_url: str) -> str | None:
    parsed = parse_github_url(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    for path in ("pyproject.toml", "setup.cfg"):
        try:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path}"
            with urllib.request.urlopen(raw_url, timeout=5) as r:
                content = r.read().decode()
                match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                if match:
                    return match.group(1)
        except Exception:
            continue
    return None


def resolve_readme_url(repo_url: str) -> str | None:
    parsed = parse_github_url(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    return f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"


def resolve_repo_url_from_pypi(package_name: str) -> str | None:
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
            info = data.get("info", {})

            # Try project_urls first
            urls = info.get("project_urls", {}) or {}
            for key in ("Source", "Repository", "Source Code", "Homepage"):
                val = urls.get(key, "")
                if "github.com" in val:
                    return val

            # Try home_page
            hp = info.get("home_page", "") or ""
            if "github.com" in hp:
                return hp

            # Last resort: scan the long description for GitHub repo URLs
            desc = info.get("description", "") or ""
            # Match github.com/org/repo but not /issues, /discussions, /pulls etc
            matches = re.findall(r'https://github\.com/([^/\s\)]+)/([^/\s\)\]#]+)', desc)
            seen = set()
            for org, repo_name in matches:
                repo_name = repo_name.rstrip(".")
                key = f"{org}/{repo_name}"
                if key not in seen and repo_name not in ("issues", "discussions", "pulls", "actions", "releases", "wiki"):
                    seen.add(key)
                    return f"https://github.com/{org}/{repo_name}"
    except Exception:
        pass
    return None


def _verify_repo_has_package(repo_url: str, expected_name: str) -> bool:
    """Check if a GitHub repo's pyproject.toml declares the expected package name."""
    actual_name = resolve_pypi_name_from_repo(repo_url)
    return actual_name is not None and actual_name == expected_name


def _try_org_repos(org: str, package_name: str) -> str | None:
    """Search an org's repos for one whose pyproject.toml has the given package name."""
    # Common patterns: pkg-name -> org/pkg-name, org/python-pkg-name, org/pkg-name-sdk
    candidates = [
        f"https://github.com/{org}/{package_name}",
        f"https://github.com/{org}/{package_name}-sdk",
        f"https://github.com/{org}/{package_name}-python",
        f"https://github.com/python-{package_name}/{package_name}",
    ]
    for url in candidates:
        name = resolve_pypi_name_from_repo(url)
        if name == package_name:
            return url
    return None


def resolve_target(target: str) -> dict:
    """Resolve a target (package name or GitHub URL) to package_name, repo_url, readme_url."""
    result = {
        "package_name": target,
        "repo_url": None,
        "readme_url": None,
    }

    if is_github_url(target):
        pypi_name = resolve_pypi_name_from_repo(target)
        if pypi_name:
            result["package_name"] = pypi_name
        else:
            parsed = parse_github_url(target)
            result["package_name"] = parsed[1] if parsed else target
        result["repo_url"] = target.rstrip("/").replace(".git", "")
        result["readme_url"] = resolve_readme_url(target)
    else:
        result["package_name"] = target
        repo = resolve_repo_url_from_pypi(target)

        if repo:
            # Verify this repo actually contains this package
            if _verify_repo_has_package(repo, target):
                result["repo_url"] = repo
                result["readme_url"] = resolve_readme_url(repo)
            else:
                # Wrong repo (e.g. monorepo). Try common naming patterns.
                parsed = parse_github_url(repo)
                if parsed:
                    org = parsed[0]
                    alt = _try_org_repos(org, target)
                    if alt:
                        result["repo_url"] = alt
                        result["readme_url"] = resolve_readme_url(alt)
                    else:
                        # Use the repo anyway, better than nothing
                        result["repo_url"] = repo
                        result["readme_url"] = resolve_readme_url(repo)

    return result
