from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pandas as pd
import numpy as np
import tempfile
import os
from filelock import FileLock
from datetime import datetime

app = FastAPI(title="SMAPE Leaderboard")

# -----------------------------
# CORS
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Paths
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUAL_FILE = os.path.join(BASE_DIR, "actual.csv")  # adjust path if needed
LEADERBOARD_FILE = os.path.join(BASE_DIR, "leaderboard.csv")
LOCK_FILE = os.path.join(BASE_DIR, "leaderboard.lock")

# -----------------------------
# Helper: initialize leaderboard
# -----------------------------
def init_leaderboard():
    if not os.path.exists(LEADERBOARD_FILE):
        df = pd.DataFrame(columns=["id","user","smape","timestamp"])
        df.to_csv(LEADERBOARD_FILE, index=False)

init_leaderboard()

# -----------------------------
# SMAPE calculation
# -----------------------------
def calculate_smape(actual_path, predicted_path):
    actual_df = pd.read_csv(actual_path)
    predicted_df = pd.read_csv(predicted_path)

    if "final_seatcount" not in actual_df.columns:
        raise ValueError("actual.csv missing 'final_seatcount'")
    if "final_seatcount" not in predicted_df.columns:
        raise ValueError("Predicted missing 'final_seatcount'")
    if len(actual_df) != len(predicted_df):
        raise ValueError("Row count mismatch")

    actual = actual_df["final_seatcount"].values
    predicted = predicted_df["final_seatcount"].values

    smape = np.mean(np.abs(predicted - actual) / ((np.abs(actual) + np.abs(predicted)) / 2)) * 100
    return round(smape, 2)

# -----------------------------
# Append leaderboard
# -----------------------------
def append_leaderboard(user, smape):
    with FileLock(LOCK_FILE):
        # Initialize if missing
        if not os.path.exists(LEADERBOARD_FILE):
            init_leaderboard()

        df = pd.read_csv(LEADERBOARD_FILE)

        # Ensure 'id' column exists
        if "id" not in df.columns:
            df.insert(0, "id", range(1, len(df)+1))

        new_id = df["id"].max() + 1 if not df.empty else 1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df.loc[len(df)] = [new_id, user, smape, timestamp]
        df.to_csv(LEADERBOARD_FILE, index=False)

# -----------------------------
# API routes
# -----------------------------
@app.post("/upload")
async def upload_prediction(user_name: str = Form(...), file: UploadFile = None):
    if not user_name or not file:
        return {"error": "Missing name or file"}

    temp_path = None
    try:
        suffix = "." + file.filename.split(".")[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.file.read())
            temp_path = tmp.name

        smape_score = calculate_smape(ACTUAL_FILE, temp_path)
        append_leaderboard(user_name, smape_score)
        return {"user": user_name, "smape": smape_score}

    except Exception as e:
        return {"error": str(e)}

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@app.get("/leaderboard")
def get_leaderboard():
    if not os.path.exists(LEADERBOARD_FILE):
        return []

    df = pd.read_csv(LEADERBOARD_FILE)
    if df.empty:
        return []

    df = df.sort_values("smape", ascending=True).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df.to_dict(orient="records")

# -----------------------------
# Mount frontend
# -----------------------------
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
