#!/usr/bin/env python3
"""Simple script to test database connection and check available tables."""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from modules import db

def main():
    try:
        # Test database connection
        print("Testing database connection...")
        creds = db.load_creds(None)
        print(f"Database credentials loaded: {creds['host']}:{creds['port']}/{creds['database']}")
        
        conn = db.connect(creds)
        print("✓ Successfully connected to database!")
        
        # Check available tables
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        
        tables = cursor.fetchall()
        print(f"\nAvailable tables ({len(tables)}):")
        for table in tables:
            print(f"  - {table[0]}")
            
        # Check if tides_cand table exists and has data
        if any('tides_cand' in table for table in tables):
            cursor.execute("SELECT COUNT(*) FROM tides_cand;")
            count = cursor.fetchone()[0]
            print(f"\ntides_cand table has {count} rows")
            
            if count > 0:
                cursor.execute("SELECT tides_id FROM tides_cand LIMIT 5;")
                sample_ids = cursor.fetchall()
                print("Sample tides_ids:", [row[0] for row in sample_ids])
        else:
            print("\n⚠️  tides_cand table does not exist")
            
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()