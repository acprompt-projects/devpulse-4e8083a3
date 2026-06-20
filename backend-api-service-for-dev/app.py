from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import sqlite3
import json
import uuid
from datetime import datetime, timedelta
import random

app = FastAPI(title="DevPulse API", version="1.0.0",
              description="Backend API for developer activity metrics — commits, PRs, review stats, deployment frequency")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = "devpulse.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS developers (
        id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT, avatar_url TEXT, team TEXT
    );
    CREATE TABLE IF NOT EXISTS commits (
        id TEXT PRIMARY KEY, developer_id TEXT NOT NULL, repo TEXT NOT NULL,
        message TEXT, additions INTEGER DEFAULT 0, deletions INTEGER DEFAULT 0,
        timestamp TEXT NOT NULL, branch TEXT DEFAULT 'main',
        FOREIGN KEY (developer_id) REFERENCES developers(id)
    );
    CREATE TABLE IF NOT EXISTS pull_requests (
        id TEXT PRIMARY KEY, developer_id TEXT NOT NULL, repo TEXT NOT NULL,
        title TEXT, state TEXT DEFAULT 'open', additions INTEGER DEFAULT 0,
        deletions INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
        created_at TEXT NOT NULL, merged_at TEXT, review_count INTEGER DEFAULT 0,
        FOREIGN KEY (developer_id) REFERENCES developers(id)
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id TEXT PRIMARY KEY, reviewer_id TEXT NOT NULL, pull_request_id TEXT NOT NULL,
        state TEXT DEFAULT 'pending', body TEXT, submitted_at TEXT NOT NULL,
        FOREIGN KEY (reviewer_id) REFERENCES developers(id),
        FOREIGN KEY (pull_request_id) REFERENCES pull_requests(id)
    );
    CREATE TABLE IF NOT EXISTS deployments (
        id TEXT PRIMARY KEY, developer_id TEXT NOT NULL, repo TEXT NOT NULL,
        environment TEXT DEFAULT 'production', status TEXT DEFAULT 'success',
        duration_seconds INTEGER, deployed_at TEXT NOT NULL,
        FOREIGN KEY (developer_id) REFERENCES developers(id)
    );
    """)
    conn.commit()
    c.execute("SELECT COUNT(*) FROM developers")
    if c.fetchone()[0] == 0:
        seed_data(conn)
    conn.close()

def seed_data(conn):
    devs = [
        ("d1","alice","Alice Chen","https://avatars.githubusercontent.com/alice","platform"),
        ("d2","bob","Bob Martinez","https://avatars.githubusercontent.com/bob","platform"),
        ("d3","carol","Carol Nguyen","https://avatars.githubusercontent.com/carol","frontend"),
        ("d4","dave","Dave Kim","https://avatars.githubusercontent.com/dave","frontend"),
        ("d5","eve","Eve Patel","https://avatars.githubusercontent.com/eve","backend"),
    ]
    conn.executemany("INSERT INTO developers VALUES(?,?,?,?,?)", devs)
    repos = ["devpulse/api", "devpulse/web", "devpulse/infra", "devpulse/cli", "devpulse/docs"]
    now = datetime.utcnow()
    for i in range(80):
        ts = (now - timedelta(hours=random.randint(0,720))).isoformat()
        conn.execute("INSERT INTO commits VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), random.choice(devs)[0], random.choice(repos),
             random.choice(["Fix auth bug","Add metrics endpoint","Update deps","Refactor utils",
                "Add caching layer","Fix CI pipeline","Update README","Implement search"]),
             random.randint(5,500), random.randint(1,200), ts, random.choice(["main","develop","feature/x"])))
    for i in range(30):
        created = (now - timedelta(hours=random.randint(0,720))).isoformat()
        state = random.choice(["open","merged","closed"])
        merged = (now - timedelta(hours=random.randint(0,200))).isoformat() if state=="merged" else None
        conn.execute("INSERT INTO pull_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), random.choice(devs)[0], random.choice(repos),
             random.choice(["Feature: activity feed","Fix: pagination bug","Refactor: DB layer",
                "Add: deployment tracking","Update: API docs","Improve: error handling"]),
             state, random.randint(10,800), random.randint(5,300), random.randint(0,15),
             created, merged, random.randint(0,5)))
    for i in range(25):
        ts = (now - timedelta(hours=random.randint(0,720))).isoformat()
        conn.execute("INSERT INTO reviews VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), random.choice(devs)[0], str(uuid.uuid4()),
             random.choice(["approved","changes_requested","commented"]), "LGTM" if random.random()>0.5 else "Please update", ts))
    for i in range(20):
        ts = (now - timedelta(hours=random.randint(0,720))).isoformat()
        conn.execute("INSERT INTO deployments VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), random.choice(devs)[0], random.choice(repos),
             random.choice(["production","staging","dev"]), random.choice(["success","failed","rollback"]),
             random.randint(30,600), ts))
    conn.commit()

init_db()

# --- Response helpers ---
def row_to_dict(row):
    return dict(row) if row else None

def rows_to_dict(rows):
    return [dict(r) for r in rows]

# --- Endpoints ---

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/developers")
def list_developers(team: Optional[str] = Query(None)):
    conn = get_db(); c = conn.cursor()
    if team:
        c.execute("SELECT * FROM developers WHERE team=?", (team,))
    else:
        c.execute("SELECT * FROM developers")
    r = rows_to_dict(c.fetchall()); conn.close(); return r

@app.get("/api/developers/{developer_id}")
def get_developer(developer_id: str):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM developers WHERE id=?", (developer_id,))
    r = row_to_dict(c.fetchone()); conn.close()
    if not r: return {"error": "Developer not found"}, 404
    return r

@app.get("/api/commits")
def list_commits(developer_id: Optional[str]=None, repo: Optional[str]=None,
                 branch: Optional[str]=None, since: Optional[str]=None,
                 search: Optional[str]=None, limit: int=Query(50, le=200),
                 offset: int=Query(0, ge=0)):
    conn = get_db(); c = conn.cursor()
    q = "SELECT c.*, d.username, d.display_name FROM commits c JOIN developers d ON c.developer_id=d.id WHERE 1=1"
    p = []
    if developer_id: q+=" AND c.developer_id=?"; p.append(developer_id)
    if repo: q+=" AND c.repo=?"; p.append(repo)
    if branch: q+=" AND c.branch=?"; p.append(branch)
    if since: q+=" AND c.timestamp>=?"; p.append(since)
    if search: q+=" AND c.message LIKE ?"; p.append(f"%{search}%")
    q+=" ORDER BY c.timestamp DESC LIMIT ? OFFSET ?"; p.extend([limit, offset])
    c.execute(q, p); r = rows_to_dict(c.fetchall()); conn.close(); return r

@app.get("/api/pull-requests")
def list_prs(developer_id: Optional[str]=None, repo: Optional[str]=None,
             state: Optional[str]=None, search: Optional[str]=None,
             limit: int=Query(50, le=200), offset: int=Query(0, ge=0)):
    conn = get_db(); c = conn.cursor()
    q = "SELECT pr.*, d.username, d.display_name FROM pull_requests pr JOIN developers d ON pr.developer_id=d.id WHERE 1=1"
    p = []
    if developer_id: q+=" AND pr.developer_id=?"; p.append(developer_id)
    if repo: q+=" AND pr.repo=?"; p.append(repo)
    if state: q+=" AND pr.state=?"; p.append(state)
    if search: q+=" AND pr.title LIKE ?"; p.append(f"%{search}%")
    q+=" ORDER BY pr.created_at DESC LIMIT ? OFFSET ?"; p.extend([limit, offset])
    c.execute(q, p); r = rows_to_dict(c.fetchall()); conn.close(); return r

@app.get("/api/reviews")
def list_reviews(reviewer_id: Optional[str]=None, state: Optional[str]=None,
                 limit: int=Query(50, le=200), offset: int=Query(0, ge=0)):
    conn = get_db(); c = conn.cursor()
    q = "SELECT rv.*, d.username, d.display_name FROM reviews rv JOIN developers d ON rv.reviewer_id=d.id WHERE 1=1"
    p = []
    if reviewer_id: q+=" AND rv.reviewer_id=?"; p.append(reviewer_id)
    if state: q+=" AND rv.state=?"; p.append(state)
    q+=" ORDER BY rv.submitted_at DESC LIMIT ? OFFSET ?"; p.extend([limit, offset])
    c.execute(q, p); r = rows_to_dict(c.fetchall()); conn.close(); return r

@app.get("/api/deployments")
def list_deployments(developer_id: Optional[str]=None, repo: Optional[str]=None,
                     environment: Optional[str]=None, status: Optional[str]=None,
                     limit: int=Query(50, le=200), offset: int=Query(0, ge=0)):
    conn = get_db(); c = conn.cursor()
    q = "SELECT dp.*, d.username, d.display_name FROM deployments dp JOIN developers d ON dp.developer_id=d.id WHERE 1=1"
    p = []
    if developer_id: q+=" AND dp.developer_id=?"; p.append(developer_id)
    if repo: q+=" AND dp.repo=?"; p.append(repo)
    if environment: q+=" AND dp.environment=?"; p.append(environment)
    if status: q+=" AND dp.status=?"; p.append(status)
    q+=" ORDER BY dp.deployed_at DESC LIMIT ? OFFSET ?"; p.extend([limit, offset])
    c.execute(q, p); r = rows_to_dict(c.fetchall()); conn.close(); return r

@app.get("/api/metrics/summary")
def metrics_summary(team: Optional[str]=None, days: int=Query(30)):
    conn = get_db(); c = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    base_join = "JOIN developers d ON t.developer_id=d.id" if team else ""
    team_where = " AND d.team=?" if team else ""
    tp = [team] if team else []
    c.execute(f"SELECT COUNT(*) FROM commits t {base_join} WHERE t.timestamp>=?{team_where}", [since]+tp)
    commit_count = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM pull_requests t {base_join} WHERE t.created_at>=?{team_where}", [since]+tp)
    pr_count = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM reviews t {base_join} WHERE t.submitted_at>=?{team_where}", [since]+tp)
    review_count = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM deployments t {base_join} WHERE t.deployed_at>=?{team_where}", [since]+tp)
    deploy_count = c.fetchone()[0]
    c.execute(f"SELECT SUM(t.additions) as total_add, SUM(t.deletions) as total_del FROM commits t {base_join} WHERE t.timestamp>=?{team_where}", [since]+tp)
    row = c.fetchone(); code_changes = {"additions": row[0] or 0, "deletions": row[1] or 0}
    c.execute(f"SELECT t.status, COUNT(*) as cnt FROM deployments t {base_join} WHERE t.deployed_at>=?{team_where} GROUP BY t.status", [since]+tp)
    deploy_breakdown = {r[0]: r[1] for r in c.fetchall()}
    c.execute(f"SELECT AVG(t.duration_seconds) FROM deployments t {base_join} WHERE t.deployed_at>=?{team_where}", [since]+tp)
    avg_deploy_duration = c.fetchone()[0] or 0
    conn.close()
    return {"period_days": days, "commits": commit_count, "pull_requests": pr_count,
            "reviews": review_count, "deployments": deploy_count, "code_changes": code_changes,
            "deploy_breakdown": deploy_breakdown, "avg_deploy_duration_seconds": round(avg_deploy_duration, 1)}

@app.get("/api/metrics/developer/{developer_id}")
def developer_metrics(developer_id: str, days: int=Query(30)):
    conn = get_db(); c = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    c.execute("SELECT * FROM developers WHERE id=?", (developer_id,))
    dev = row_to_dict(c.fetchone())
    if not dev: return {"error": "Not found"}
    c.execute("SELECT COUNT(*), SUM(additions), SUM(deletions) FROM commits WHERE developer_id=? AND timestamp>=?",
              (developer_id, since))
    row = c.fetchone()
    commits = {"count": row[0], "additions": row[1] or 0, "deletions": row[2] or 0}
    c.execute("SELECT COUNT(*) FROM pull_requests WHERE developer_id=? AND created_at>=?", (developer_id, since))
    prs = {"count": c.fetchone()[0]}
    c.execute("SELECT state, COUNT(*) FROM reviews WHERE reviewer_id=? AND submitted_at>=? GROUP BY state",
              (developer_id, since))
    reviews = {r[0]: r[1] for r in c.fetchall()}
    c.execute("SELECT environment, COUNT(*), AVG(duration_seconds) FROM deployments WHERE developer_id=? AND deployed_at>=? GROUP BY environment",
              (developer_id, since))
    deploys = [{"environment": r[0], "count": r[1], "avg_duration": round(r[2] or 0, 1)} for r in c.fetchall()]
    conn.close()
    return {"developer": dev, "period_days": days, "commits": commits, "pull_requests": prs,
            "reviews": reviews, "deployments": deploys}

@app.get("/api/activity-feed")
def activity_feed(team: Optional[str]=None, search: Optional[str]=None,
                  limit: int=Query(50, le=200), offset: int=Query(0, ge=0)):
    conn = get_db(); c = conn.cursor()
    items = []
    q_commits = """SELECT c.id, 'commit' as type, d.username, d.display_name, c.repo, c.message as title,
                   c.additions, c.deletions, c.timestamp as created_at FROM commits c
                   JOIN developers d ON c.developer_id=d.id WHERE 1=1"""
    q_prs = """SELECT pr.id, 'pull_request' as type, d.username, d.display_name, pr.repo, pr.title,
               pr.state, pr.additions, pr.deletions, pr.created_at FROM pull_requests pr
               JOIN developers d ON pr.developer_id=d.id WHERE 1=1"""
    q_deploys = """SELECT dp.id, 'deployment' as type, d.username, d.display_name, dp.repo, dp.environment as title,
                   dp.status, dp.duration_seconds, dp.deployed_at as created_at FROM deployments dp
                   JOIN developers d ON dp.developer_id=d.id WHERE 1=1"""
    for q_base in [q_commits, q_prs, q_deploys]:
        q = q_base; p = []
        if team: q += " AND d.team=?"; p.append(team)
        if search: q += " AND (d.username LIKE ? OR title LIKE ? OR repo LIKE ?)"
        p.extend([f"%{search}%"]*3) if search else None
        c.execute(q, p)
        items.extend([dict(r) for r in c.fetchall()])
    items.sort(key=lambda x: x["created_at"], reverse=True)
    conn.close()
    return items[offset:offset+limit]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)