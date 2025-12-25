from fastapi import FastAPI, UploadFile, Form, Response, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import pandas as pd
import numpy as np
import tempfile, os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Optional
import hashlib
import secrets
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="SMAPE Leaderboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUAL_FILE = os.path.join(BASE_DIR, "actual.csv")
MAX_SUBMISSIONS_PER_TEAM = 10

# Get DATABASE_URL from environment variable
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# Admin team ID
ADMIN_TEAM_ID = 'admin2025'

def calculate_smape(actual, predicted):
    df_actual = pd.read_csv(actual)["final_service_units"].values
    df_predicted = pd.read_csv(predicted)["final_service_units"].values
    
    # -------- RMSE --------
    rmse = np.sqrt(np.mean((df_actual - df_predicted) ** 2))
    
    # -------- SMAPE --------
    smape_val = np.mean(np.abs((df_actual - df_predicted) / (df_actual + 1e-8))) * 100
    
    # -------- HUBER LOSS --------
    delta = 1.0
    errors = df_actual - df_predicted
    huber_loss = np.mean(
        np.where(np.abs(errors) <= delta,
                 0.5 * errors ** 2,
                 delta * (np.abs(errors) - 0.5 * delta))
    )
    
    # -------- FINAL LEADERBOARD SCORE --------
    smape = smape_val * (1 + rmse / 12.5) + huber_loss


    print("rmse:", rmse)
    print("smape:", smape_val)
    print("huber_loss:", huber_loss)
    print("final:", smape)


    return float(round(smape, 2))

def append_score(user, score):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO submissions ("user", smape, timestamp) VALUES (%s, %s, %s)',
        (user, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    cur.close()
    conn.close()

@app.post("/upload")
async def upload(file: UploadFile = None, auth_token: Optional[str] = Cookie(None)):
    # Verify authentication
    if not auth_token:
        return {"error": "Not authenticated. Please login first."}
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = %s',
        (auth_token,)
    )
    auth = cur.fetchone()
    
    if not auth:
        cur.close()
        conn.close()
        return {"error": "Invalid authentication. Please login again."}
    
    team_id = auth["team_id"]
    team_name = auth["team_name"]
    
    # Check submission limit
    cur.execute(
        'SELECT COUNT(*) as count FROM team_submissions WHERE team_id = %s',
        (team_id,)
    )
    submission_count = cur.fetchone()["count"]
    
    if submission_count >= MAX_SUBMISSIONS_PER_TEAM:
        cur.close()
        conn.close()
        return {"error": f"Submission limit reached. Maximum {MAX_SUBMISSIONS_PER_TEAM} submissions allowed per team."}
    
    cur.close()
    conn.close()
    
    temp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as t:
            t.write(file.file.read())
            temp = t.name
        score = calculate_smape(ACTUAL_FILE, temp)
        
        # Check if team already has a submission in leaderboard
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            'SELECT id, smape FROM submissions WHERE "user" = %s',
            (team_id,)
        )
        existing = cur.fetchone()
        
        is_best = False
        if existing:
            # Only update if new score is better (lower SMAPE)
            if score < existing["smape"]:
                cur.execute(
                    'UPDATE submissions SET smape = %s, timestamp = %s WHERE id = %s',
                    (score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), existing["id"])
                )
                is_best = True
        else:
            # First submission - insert new entry
            cur.execute(
                'INSERT INTO submissions ("user", smape, timestamp) VALUES (%s, %s, %s)',
                (team_id, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            is_best = True
        
        # Track this submission for limit counting
        cur.execute(
            'INSERT INTO team_submissions (team_id, smape, timestamp) VALUES (%s, %s, %s)',
            (team_id, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        
        # Get updated submission count
        cur.execute(
            'SELECT COUNT(*) as count FROM team_submissions WHERE team_id = %s',
            (team_id,)
        )
        new_count = cur.fetchone()["count"]
        
        conn.commit()
        cur.close()
        conn.close()
        
        remaining = MAX_SUBMISSIONS_PER_TEAM - new_count
        return {"smape": score, "team_name": team_name, "is_best": is_best, "submissions_remaining": remaining}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)

@app.get("/leaderboard")
def leaderboard():
    conn = get_db_connection()
    cur = conn.cursor()
    # Join with teams table to get team_name for display
    cur.execute('''
        SELECT s.id, s."user" as team_id, t.team_name, s.smape, s.timestamp 
        FROM submissions s 
        LEFT JOIN teams t ON s."user" = t.team_id 
        ORDER BY s.smape ASC
    ''')
    rows = cur.fetchall()
    cur.close()
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

@app.get("/submissions/remaining")
def get_remaining_submissions(auth_token: Optional[str] = Cookie(None)):
    """Get remaining submission count for the logged-in team"""
    if not auth_token:
        return {"error": "Not authenticated", "remaining": 0}
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT team_id FROM auth_tokens WHERE token = %s',
        (auth_token,)
    )
    auth = cur.fetchone()
    
    if not auth:
        cur.close()
        conn.close()
        return {"error": "Invalid token", "remaining": 0}
    
    team_id = auth["team_id"]
    cur.execute(
        'SELECT COUNT(*) as count FROM team_submissions WHERE team_id = %s',
        (team_id,)
    )
    count = cur.fetchone()["count"]
    cur.close()
    conn.close()
    
    return {"remaining": MAX_SUBMISSIONS_PER_TEAM - count, "used": count, "max": MAX_SUBMISSIONS_PER_TEAM}

@app.get("/user/{name}")
def user_history(name: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT id, "user", smape, timestamp FROM submissions WHERE LOWER("user") = LOWER(%s) ORDER BY timestamp DESC',
        (name,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r["id"], "user": r["user"], "smape": r["smape"], "timestamp": r["timestamp"]} for r in rows]

@app.post("/leaderboard/reset")
def reset(auth_token: Optional[str] = Cookie(None)):
    # Verify admin authentication
    if not auth_token:
        return {"success": False, "error": "Not authenticated"}
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT team_id FROM auth_tokens WHERE token = %s',
        (auth_token,)
    )
    auth = cur.fetchone()
    
    if not auth or auth["team_id"] != ADMIN_TEAM_ID:
        cur.close()
        conn.close()
        return {"success": False, "error": "Admin access required"}
    
    # Clear both leaderboard and submission tracking
    cur.execute('DELETE FROM submissions')
    cur.execute('DELETE FROM team_submissions')
    cur.execute('DELETE FROM submission_history')
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True, "status": "Leaderboard and all submissions reset"}

@app.get("/history/{session_id}")
def get_history(session_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT id, team, smape, timestamp FROM submission_history WHERE session_id = %s ORDER BY id ASC',
        (session_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r["id"], "team": r["team"], "smape": r["smape"], "timestamp": r["timestamp"]} for r in rows]

@app.post("/history/{session_id}")
def add_history(session_id: str, team: str = Form(...), smape: float = Form(...), timestamp: str = Form(...)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO submission_history (session_id, team, smape, timestamp) VALUES (%s, %s, %s, %s)',
        (session_id, team, smape, timestamp)
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "Added to history"}

@app.delete("/history/{session_id}")
def clear_history(session_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM submission_history WHERE session_id = %s', (session_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "History cleared"}

# Authentication endpoints
@app.post("/auth/login")
def login(response: Response, team_id: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT * FROM teams WHERE team_id = %s',
        (team_id,)
    )
    team = cur.fetchone()
    
    if not team:
        cur.close()
        conn.close()
        return {"success": False, "error": "Team not found"}
    
    if team["password_hash"] != hash_password(password):
        cur.close()
        conn.close()
        return {"success": False, "error": "Invalid password"}
    
    # Generate auth token
    token = secrets.token_hex(32)
    cur.execute(
        'INSERT INTO auth_tokens (team_id, token, created_at) VALUES (%s, %s, %s)',
        (team_id, token, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    cur.close()
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
        cur = conn.cursor()
        cur.execute('DELETE FROM auth_tokens WHERE token = %s', (auth_token,))
        conn.commit()
        cur.close()
        conn.close()
    
    response.delete_cookie("auth_token")
    return {"success": True}

@app.get("/auth/me")
def get_current_user(auth_token: Optional[str] = Cookie(None)):
    """Get current logged-in user from cookie"""
    if not auth_token:
        return {"authenticated": False}
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = %s',
        (auth_token,)
    )
    auth = cur.fetchone()
    cur.close()
    conn.close()
    
    if auth:
        is_admin = auth["team_id"] == ADMIN_TEAM_ID
        return {"authenticated": True, "team_id": auth["team_id"], "team_name": auth["team_name"], "is_admin": is_admin}
    return {"authenticated": False}

@app.get("/auth/verify/{token}")
def verify_token(token: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT t.team_id, t.team_name FROM auth_tokens a JOIN teams t ON a.team_id = t.team_id WHERE a.token = %s',
        (token,)
    )
    auth = cur.fetchone()
    cur.close()
    conn.close()
    
    if auth:
        return {"valid": True, "team_id": auth["team_id"], "team_name": auth["team_name"]}
    return {"valid": False}

@app.get("/auth/teams")
def get_teams():
    """Get list of all registered teams (for admin purposes)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT team_id, team_name, created_at FROM teams')
    teams = cur.fetchall()
    cur.close()
    conn.close()
    return [{"team_id": t["team_id"], "team_name": t["team_name"], "created_at": t["created_at"]} for t in teams]

FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
