import requests


def get_repo_info(owner: str, repo: str) -> dict:
    """Fetch basic info (stargazers, forks, description) for a GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return {
        "stargazers_count": data.get("stargazers_count"),
        "forks_count": data.get("forks_count"),
        "description": data.get("description"),
    }
