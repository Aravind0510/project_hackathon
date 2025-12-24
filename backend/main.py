from fastapi import FastAPI, UploadFile, Form, Response, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import pandas as pd
import numpy as np
import tempfile, os
import sqlite3
from datetime import datetime
from typing import Optional
import hashlib
import secrets

app = FastAPI(title="SMAPE Leaderboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUAL_FILE = os.path.join(BASE_DIR, "actual.csv")
DB_FILE = os.path.join(BASE_DIR, "leaderboard.db")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            smape REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS submission_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            team TEXT NOT NULL,
            smape REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    # Create teams table for authentication
    conn.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id TEXT UNIQUE NOT NULL,
            team_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    # Create auth tokens table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    
    # Insert test teams if they don't exist
    try:
        conn.execute(
            'INSERT OR IGNORE INTO teams (team_id, team_name, password_hash, created_at) VALUES (?, ?, ?, ?)',
            ('1234', 'Team Alpha', hash_password('password123'), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.execute(
            'INSERT OR IGNORE INTO teams (team_id, team_name, password_hash, created_at) VALUES (?, ?, ?, ?)',
            ('4321', 'Team Beta', hash_password('password456'), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
    except:
        pass
    conn.close()

init_db()

def calculate_smape(actual, predicted):
    a = pd.read_csv(actual)["final_service_units"].values
    p = pd.read_csv(predicted)["final_service_units"].values
    smape = np.mean(np.abs(p - a) / ((np.abs(a) + np.abs(p)) / 2)) * 100
    return round(smape, 2)

def append_score(user, score):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO submissions (user, smape, timestamp) VALUES (?, ?, ?)',
        (user, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

@app.post("/upload")
async def upload(file: UploadFile = None, auth_token: Optional[str] = Cookie(None)):
    # Verify authentication
    if not auth_token:
        return {"error": "Not authenticated. Please login first."}
    
    conn = get_db_connection()
    auth = conn.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = ?',
        (auth_token,)
    ).fetchone()
    
    if not auth:
        conn.close()
        return {"error": "Invalid authentication. Please login again."}
    
    team_id = auth["team_id"]
    team_name = auth["team_name"]
    conn.close()
    
    temp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as t:
            t.write(file.file.read())
            temp = t.name
        score = calculate_smape(ACTUAL_FILE, temp)
        
        # Check if team already has a submission in leaderboard
        conn = get_db_connection()
        existing = conn.execute(
            'SELECT id, smape FROM submissions WHERE user = ?',
            (team_id,)
        ).fetchone()
        
        is_best = False
        if existing:
            # Only update if new score is better (lower SMAPE)
            if score < existing["smape"]:
                conn.execute(
                    'UPDATE submissions SET smape = ?, timestamp = ? WHERE id = ?',
                    (score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), existing["id"])
                )
                is_best = True
        else:
            # First submission - insert new entry
            conn.execute(
                'INSERT INTO submissions (user, smape, timestamp) VALUES (?, ?, ?)',
                (team_id, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            is_best = True
        
        conn.commit()
        conn.close()
        
        return {"smape": score, "team_name": team_name, "is_best": is_best}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)

@app.get("/leaderboard")
def leaderboard():
    conn = get_db_connection()
    # Join with teams table to get team_name for display
    rows = conn.execute('''
        SELECT s.id, s.user as team_id, t.team_name, s.smape, s.timestamp 
        FROM submissions s 
        LEFT JOIN teams t ON s.user = t.team_id 
        ORDER BY s.smape ASC
    ''').fetchall()
    conn.close()
    result = []
    for rank, row in enumerate(rows, 1):
        result.append({
            "rank": rank,
            "id": row["id"],
            "team_id": row["team_id"],
            "user": row["team_name"] or row["team_id"],  # Fallback to team_id if no name
            "smape": row["smape"],
            "timestamp": row["timestamp"]
        })
    return result

@app.get("/user/{name}")
def user_history(name: str):
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT id, user, smape, timestamp FROM submissions WHERE LOWER(user) = LOWER(?) ORDER BY timestamp DESC',
        (name,)
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "user": r["user"], "smape": r["smape"], "timestamp": r["timestamp"]} for r in rows]

@app.post("/leaderboard/reset")
def reset():
    conn = get_db_connection()
    conn.execute('DELETE FROM submissions')
    conn.commit()
    conn.close()
    return {"status": "Leaderboard reset"}

@app.get("/history/{session_id}")
def get_history(session_id: str):
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT id, team, smape, timestamp FROM submission_history WHERE session_id = ? ORDER BY id ASC',
        (session_id,)
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "team": r["team"], "smape": r["smape"], "timestamp": r["timestamp"]} for r in rows]

@app.post("/history/{session_id}")
def add_history(session_id: str, team: str = Form(...), smape: float = Form(...), timestamp: str = Form(...)):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO submission_history (session_id, team, smape, timestamp) VALUES (?, ?, ?, ?)',
        (session_id, team, smape, timestamp)
    )
    conn.commit()
    conn.close()
    return {"status": "Added to history"}

@app.delete("/history/{session_id}")
def clear_history(session_id: str):
    conn = get_db_connection()
    conn.execute('DELETE FROM submission_history WHERE session_id = ?', (session_id,))
    conn.commit()
    conn.close()
    return {"status": "History cleared"}

# Authentication endpoints
@app.post("/auth/login")
def login(response: Response, team_id: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    team = conn.execute(
        'SELECT * FROM teams WHERE team_id = ?',
        (team_id,)
    ).fetchone()
    
    if not team:
        conn.close()
        return {"success": False, "error": "Team not found"}
    
    if team["password_hash"] != hash_password(password):
        conn.close()
        return {"success": False, "error": "Invalid password"}
    
    # Generate auth token
    token = secrets.token_hex(32)
    conn.execute(
        'INSERT INTO auth_tokens (team_id, token, created_at) VALUES (?, ?, ?)',
        (team_id, token, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    # Set HTTP-only cookie
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400  # 24 hours
    )
    
    return {
        "success": True,
        "team_name": team["team_name"],
        "team_id": team_id
    }

@app.post("/auth/logout")
def logout(response: Response, auth_token: Optional[str] = Cookie(None)):
    if auth_token:
        conn = get_db_connection()
        conn.execute('DELETE FROM auth_tokens WHERE token = ?', (auth_token,))
        conn.commit()
        conn.close()
    
    response.delete_cookie("auth_token")
    return {"success": True}

@app.get("/auth/me")
def get_current_user(auth_token: Optional[str] = Cookie(None)):
    """Get current logged-in user from cookie"""
    if not auth_token:
        return {"authenticated": False}
    
    conn = get_db_connection()
    auth = conn.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = ?',
        (auth_token,)
    ).fetchone()
    conn.close()
    
    if auth:
        return {"authenticated": True, "team_id": auth["team_id"], "team_name": auth["team_name"]}
    return {"authenticated": False}

@app.get("/auth/verify/{token}")
def verify_token(token: str):
    conn = get_db_connection()
    auth = conn.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = ?',
        (token,)
    ).fetchone()
    conn.close()
    
    if auth:
        return {"valid": True, "team_id": auth["team_id"], "team_name": auth["team_name"]}
    return {"valid": False}

@app.get("/auth/teams")
def get_teams():
    """Get list of all registered teams (for admin purposes)"""
    conn = get_db_connection()
    teams = conn.execute('SELECT team_id, team_name, created_at FROM teams').fetchall()
    conn.close()
    return [{"team_id": t["team_id"], "team_name": t["team_name"], "created_at": t["created_at"]} for t in teams]

FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
