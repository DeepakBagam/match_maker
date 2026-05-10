"""
Migration script to create Run Log table.
Run this once after updating the code.
"""

import sqlite3
from pathlib import Path


def migrate_run_log_table(database_path: str = "matchlayer.db"):
    """Create Run Log table if it doesn't exist."""
    
    db_path = Path(database_path)
    if not db_path.exists():
        print(f"Database not found: {database_path}")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tab_run_log'")
        exists = cursor.fetchone()
        
        if exists:
            print("[OK] Run Log table already exists")
        else:
            print("Creating Run Log table...")
            cursor.execute('''
                CREATE TABLE "tab_run_log" (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    "run_id" TEXT,
                    "start_time" TEXT,
                    "end_time" TEXT,
                    "rows_processed" TEXT,
                    "new_rows" TEXT,
                    "duplicates" TEXT,
                    "ignored" TEXT,
                    "matches" TEXT,
                    "status" TEXT,
                    "error_message" TEXT
                )
            ''')
            print("[OK] Created Run Log table")
        
        # Create index on run_id for faster lookups
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tab_run_log_run_id'")
        index_exists = cursor.fetchone()
        
        if not index_exists:
            print("Creating index on run_id...")
            cursor.execute('CREATE INDEX "idx_tab_run_log_run_id" ON "tab_run_log" ("run_id")')
            print("[OK] Created index on run_id")
        else:
            print("[OK] Index on run_id already exists")
        
        conn.commit()
        print("\n[SUCCESS] Migration completed successfully!")
        
        # Show schema
        cursor.execute("PRAGMA table_info(tab_run_log)")
        print("\nRun Log table schema:")
        for row in cursor.fetchall():
            print(f"  {row[1]} ({row[2]})")
        
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    
    db_path = sys.argv[1] if len(sys.argv) > 1 else "matchlayer.db"
    print(f"Migrating database: {db_path}\n")
    migrate_run_log_table(db_path)
