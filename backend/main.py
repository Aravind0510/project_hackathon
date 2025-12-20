from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pandas as pd
import numpy as np
import tempfile, os
from filelock import FileLock
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
LEADERBOARD_FILE = os.path.join(BASE_DIR, "leaderboard.csv")
LOCK_FILE = os.path.join(BASE_DIR, "leaderboard.lock")

def init_board():
    if not os.path.exists(LEADERBOARD_FILE):
        pd.DataFrame(
            columns=["id", "user", "smape", "timestamp"]
        ).to_csv(LEADERBOARD_FILE, index=False)

init_board()

def calculate_smape(actual, predicted):
    a = pd.read_csv(actual)["final_seatcount"].values
    p = pd.read_csv(predicted)["final_seatcount"].values
    smape = np.mean(np.abs(p - a) / ((np.abs(a) + np.abs(p)) / 2)) * 100
    return round(smape, 2)

def append_score(user, score):
    with FileLock(LOCK_FILE):
        df = pd.read_csv(LEADERBOARD_FILE)
        new_id = df["id"].max() + 1 if not df.empty else 1
        df.loc[len(df)] = [
            new_id, user, score,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        df.to_csv(LEADERBOARD_FILE, index=False)

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
    df = pd.read_csv(LEADERBOARD_FILE)
    if df.empty:
        return []
    df = df.sort_values("smape")
    df.insert(0, "rank", range(1, len(df) + 1))
    return df.to_dict(orient="records")

@app.get("/user/{name}")
def user_history(name: str):
    df = pd.read_csv(LEADERBOARD_FILE)
    return df[df["user"].str.lower() == name.lower()] \
        .sort_values("timestamp", ascending=False) \
        .to_dict(orient="records")

@app.post("/leaderboard/reset")
def reset():
    with FileLock(LOCK_FILE):
        pd.DataFrame(
            columns=["id", "user", "smape", "timestamp"]
        ).to_csv(LEADERBOARD_FILE, index=False)
    return {"status": "Leaderboard reset"}

FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
