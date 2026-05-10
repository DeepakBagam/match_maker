"""
Migration script to add Next Action and Timing columns to existing Glide Execution records.
Run this once after updating the code.
"""

import sqlite3
from pathlib import Path


def migrate_glide_execution_table(database_path: str = "matchlayer.db"):
    """Add Next Action and Timing columns to Glide Execution table."""
    
    db_path = Path(database_path)
    if not db_path.exists():
        print(f"Database not found: {database_path}")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(tab_glide_execution)")
        columns = [row[1] for row in cursor.fetchall()]
        
        print(f"Current columns in tab_glide_execution: {columns}")
        
        # Add Next Action column if it doesn't exist
        if "next_action" not in columns:
            print("Adding 'next_action' column...")
            cursor.execute('ALTER TABLE "tab_glide_execution" ADD COLUMN "next_action" TEXT DEFAULT ""')
            print("[OK] Added 'next_action' column")
        else:
            print("[OK] 'next_action' column already exists")
        
        # Add Timing column if it doesn't exist
        if "timing" not in columns:
            print("Adding 'timing' column...")
            cursor.execute('ALTER TABLE "tab_glide_execution" ADD COLUMN "timing" TEXT DEFAULT ""')
            print("[OK] Added 'timing' column")
        else:
            print("[OK] 'timing' column already exists")
        
        conn.commit()
        print("\n[SUCCESS] Migration completed successfully!")
        
        # Show updated schema
        cursor.execute("PRAGMA table_info(tab_glide_execution)")
        print("\nUpdated schema:")
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
    migrate_glide_execution_table(db_path)
