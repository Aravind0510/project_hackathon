from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pandas as pd
import numpy as np
import tempfile, os
import sqlite3
from datetime import datetime

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
    conn.commit()
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
async def upload(user_name: str = Form(...), file: UploadFile = None):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as t:
            t.write(file.file.read())
            temp = t.name
        score = calculate_smape(ACTUAL_FILE, temp)
        append_score(user_name, score)
        return {"smape": score}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)

@app.get("/leaderboard")
def leaderboard():
    conn = get_db_connection()
    rows = conn.execute('SELECT id, user, smape, timestamp FROM submissions ORDER BY smape ASC').fetchall()
    conn.close()
    result = []
    for rank, row in enumerate(rows, 1):
        result.append({
            "rank": rank,
            "id": row["id"],
            "user": row["user"],
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
    return {"status": "Leaderboard reset"}
    return {"status": "Leaderboard reset"}

FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
