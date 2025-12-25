"""
Setup script for PostgreSQL database on Render.
Run this script once to create tables and insert initial data.

Usage:
1. Make sure .env file has your DATABASE_URL
2. Run: python setup_postgres.py
"""

import psycopg2
import hashlib
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get DATABASE_URL from .env file
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def setup_database():
    print("Connecting to PostgreSQL database...")
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        print("✓ Connected successfully!")
        
        # Create tables
        print("\nCreating tables...")
        
        # Teams table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS teams (
                id SERIAL PRIMARY KEY,
                team_id TEXT UNIQUE NOT NULL,
                team_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        print("✓ Created 'teams' table")
        
        # Submissions table (leaderboard)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                "user" TEXT NOT NULL,
                smape REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        print("✓ Created 'submissions' table")
        
        # Team submissions table (for tracking submission limits)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS team_submissions (
                id SERIAL PRIMARY KEY,
                team_id TEXT NOT NULL,
                smape REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        print("✓ Created 'team_submissions' table")
        
        # Auth tokens table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS auth_tokens (
                id SERIAL PRIMARY KEY,
                team_id TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        print("✓ Created 'auth_tokens' table")
        
        # Submission history table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS submission_history (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                team TEXT NOT NULL,
                smape REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        print("✓ Created 'submission_history' table")
        
        # Insert initial teams
        print("\nInserting initial teams...")
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        teams_data = [
            ('1234', 'Team Alpha', hash_password('password123'), now),
            ('4321', 'Team Beta', hash_password('password456'), now),
            ('admin2025', 'Administrator', hash_password('TechnoForge@Admin#2025'), now),
        ]
        
        for team_id, team_name, password_hash, created_at in teams_data:
            try:
                cur.execute(
                    '''INSERT INTO teams (team_id, team_name, password_hash, created_at) 
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (team_id) DO NOTHING''',
                    (team_id, team_name, password_hash, created_at)
                )
                print(f"✓ Inserted team: {team_name} (ID: {team_id})")
            except Exception as e:
                print(f"  Team {team_id} already exists or error: {e}")
        
        # Verify data
        print("\nVerifying inserted data...")
        cur.execute("SELECT team_id, team_name, created_at FROM teams")
        rows = cur.fetchall()
        
        print("\n" + "="*50)
        print("REGISTERED TEAMS:")
        print("="*50)
        for row in rows:
            print(f"  ID: {row[0]}, Name: {row[1]}, Created: {row[2]}")
        print("="*50)
        
        cur.close()
        conn.close()
        
        print("\n✓ Database setup completed successfully!")
        print("\nYou can now update your main.py to use PostgreSQL.")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\nMake sure:")
        print("1. Your DATABASE_URL is correct")
        print("2. psycopg2 is installed: pip install psycopg2-binary")
        print("3. Your IP is allowed (Render allows all by default)")

def add_team(team_id: str, team_name: str, password: str):
    """Helper function to add a new team"""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    try:
        cur.execute(
            '''INSERT INTO teams (team_id, team_name, password_hash, created_at) 
               VALUES (%s, %s, %s, %s)''',
            (team_id, team_name, hash_password(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        print(f"✓ Added team: {team_name} (ID: {team_id})")
    except psycopg2.errors.UniqueViolation:
        print(f"✗ Team ID '{team_id}' already exists")
    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        cur.close()
        conn.close()

def list_teams():
    """List all registered teams"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    cur.execute("SELECT team_id, team_name, created_at FROM teams ORDER BY id")
    rows = cur.fetchall()
    
    print("\nRegistered Teams:")
    print("-"*50)
    for row in rows:
        print(f"  ID: {row[0]}, Name: {row[1]}, Created: {row[2]}")
    
    cur.close()
    conn.close()

def clear_leaderboard():
    """Clear all submissions (admin function)"""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    cur.execute("DELETE FROM submissions")
    cur.execute("DELETE FROM team_submissions")
    cur.execute("DELETE FROM submission_history")
    
    print("✓ Leaderboard cleared")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    print("="*50)
    print("PostgreSQL Database Setup for Hackathon Leaderboard")
    print("="*50)
    
    if DATABASE_URL == "postgresql://user:password@hostname:5432/leaderboard":
        print("\n⚠️  WARNING: You need to set your DATABASE_URL!")
        print("Edit this file and replace the DATABASE_URL with your Render PostgreSQL URL.")
        print("\nYour URL should look like:")
        print("postgresql://username:password@hostname.render.com:5432/database_name")
    else:
        setup_database()
