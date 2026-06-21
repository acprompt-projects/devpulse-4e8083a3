import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("devpulse.ingestion")

DB_PATH = Path(__file__).parent / "devpulse.db"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ActivityType(str, Enum):
    COMMIT = "commit"
    PR = "pull_request"
    ISSUE = "issue"


@dataclass
class Activity:
    external_id: str
    activity_type: ActivityType
    repo: str                       # "owner/repo"
    actor: str
    title: str
    body: str
    url: str
    created_at: str                 # ISO-8601
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class RepoSource:
    owner: str
    repo: str
    since_hours: int = 24

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def load_sources(config_path: Optional[Path] = None) -> list[RepoSource]:
    if config_path and config_path.exists():
        raw = json.loads(config_path.read_text())
        return [RepoSource(**s) for s in raw.get("sources", [])]
    # sensible default for demo
    return [RepoSource(owner="python", repo="cpython", since_hours=24)]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id     TEXT    NOT NULL UNIQUE,
    activity_type   TEXT    NOT NULL,
    repo            TEXT    NOT NULL,
    actor           TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    body            TEXT    NOT NULL DEFAULT '',
    url             TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    metadata        TEXT    NOT NULL DEFAULT '{}',
    ingested_at     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_activities_repo    ON activities(repo);
CREATE INDEX IF NOT EXISTS idx_activities_type    ON activities(activity_type);
CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at);
"""


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def persist_activities(activities: list[Activity]) -> int:
    if not activities:
        return 0
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for a in activities:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO activities
                   (external_id, activity_type, repo, actor, title, body,
                    url, created_at, metadata, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (a.external_id, a.activity_type.value, a.repo, a.actor,
                 a.title, a.body, a.url, a.created_at,
                 json.dumps(a.metadata), now),
            )
            if conn.total_changes:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    logger.info("Persisted %d new activities (skipped %d duplicates)",
                inserted, len(activities) - inserted)
    return inserted


# ---------------------------------------------------------------------------
# GitHub API Client
# ---------------------------------------------------------------------------

class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: Optional[str] = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "devpulse-ingestion/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(headers=headers, timeout=30.0)

    async def _get(self, path: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        params = dict(params or {})
        params["per_page"] = 100
        url = f"{self.BASE}{path}"
        page = 1
        while True:
            params["page"] = page
            resp = await self._client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("GH API %s returned %s", url, resp.status_code)
                break
            data = resp.json()
            if not data:
                break
            items.extend(data)
            if len(data) < 100:
                break
            page += 1
        return items

    async def fetch_commits(self, src: RepoSource) -> list[Activity]:
        since = _iso_hours_ago(src.since_hours)
        rows = await self._get(
            f"/repos/{src.full_name}/commits",
            {"since": since},
        )
        out: list[Activity] = []
        for c in rows:
            sha = c.get("sha", "")
            commit = c.get("commit", {})
            author = commit.get("author", {})
            out.append(Activity(
                external_id=f"commit:{sha}",
                activity_type=ActivityType.COMMIT,
                repo=src.full_name,
                actor=author.get("name", c.get("author", {}).get("login", "")),
                title=commit.get("message", "").split("\n", 1)[0][:256],
                body=commit.get("message", ""),
                url=c.get("html_url", ""),
                created_at=author.get("date", ""),
                metadata={"sha": sha},
            ))
        return out

    async def fetch_prs(self, src: RepoSource) -> list[Activity]:
        rows = await self._get(
            f"/repos/{src.full_name}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc"},
        )
        out: list[Activity] = []
        for p in rows:
            updated = p.get("updated_at", "")
            if _hours_between(updated) > src.since_hours:
                continue
            num = p.get("number", "")
            out.append(Activity(
                external_id=f"pr:{src.full_name}:{num}",
                activity_type=ActivityType.PR,
                repo=src.full_name,
                actor=p.get("user", {}).get("login", ""),
                title=p.get("title", ""),
                body=p.get("body", "") or "",
                url=p.get("html_url", ""),
                created_at=p.get("created_at", ""),
                metadata={
                    "number": num,
                    "state": p.get("state", ""),
                    "merged": p.get("merged", False),
                },
            ))
        return out

    async def fetch_issues(self, src: RepoSource) -> list[Activity]:
        rows = await self._get(
            f"/repos/{src.full_name}/issues",
            {"state": "all", "sort": "updated", "direction": "desc"},
        )
        out: list[Activity] = []
        for i in rows:
            if "pull_request" in i:        # GH mixes PRs into issues endpoint
                continue
            updated = i.get("updated_at", "")
            if _hours_between(updated) > src.since_hours:
                continue
            num = i.get("number", "")
            out.append(Activity(
                external_id=f"issue:{src.full_name}:{num}",
                activity_type=ActivityType.ISSUE,
                repo=src.full_name,
                actor=i.get("user", {}).get("login", ""),
                title=i.get("title", ""),
                body=i.get("body", "") or "",
                url=i.get("html_url", ""),
                created_at=i.get("created_at", ""),
                metadata={
                    "number": num,
                    "state": i.get("state", ""),
                    "labels": [l["name"] for l in i.get("labels", [])],
                },
            ))
        return out

    async def close(self):
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_hours_ago(hours: int) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _hours_between(iso_str: str) -> float:
    if not iso_str:
        return 999.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return 999.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def ingest_source(client: GitHubClient, src: RepoSource) -> list[Activity]:
    commits, prs, issues = await asyncio.gather(
        client.fetch_commits(src),
        client.fetch_prs(src),
        client.fetch_issues(src),
    )
    all_activities = commits + prs + issues
    logger.info("Fetched %d activities from %s", len(all_activities), src.full_name)
    return all_activities


async def run_ingestion_cycle(
    sources: list[RepoSource] | None = None,
    token: str | None = None,
    config_path: Path | None = None,
) -> int:
    sources = sources or load_sources(config_path)
    client = GitHubClient(token)
    try:
        all_activities: list[Activity] = []
        for src in sources:
            acts = await ingest_source(client, src)
            all_activities.extend(acts)
        return persist_activities(all_activities)
    finally:
        await client.close()


async def scheduled_ingestion(
    interval_minutes: int = 15,
    token: str | None = None,
    config_path: Path | None = None,
) -> None:
    logger.info("Starting scheduled ingestion every %d min", interval_minutes)
    while True:
        try:
            n = await run_ingestion_cycle(token=token, config_path=config_path)
            logger.info("Ingestion cycle complete — %d new activities", n)
        except Exception:
            logger.exception("Ingestion cycle failed")
        await asyncio.sleep(interval_minutes * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    token = os.environ.get("GITHUB_TOKEN")
    interval = int(os.environ.get("INGEST_INTERVAL_MIN", "15"))
    cfg = Path(os.environ.get("DEVPULSE_CONFIG", str(Path(__file__).parent / "config.json")))
    asyncio.run(scheduled_ingestion(interval_minutes=interval, token=token, config_path=cfg))


if __name__ == "__main__":
    main()