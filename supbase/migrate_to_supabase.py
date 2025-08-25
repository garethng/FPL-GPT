#!/usr/bin/env python3
"""
Migration script to transfer data from SQLite to Supabase using supabase-py
"""
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def get_sqlite_connection():
    """Get SQLite database connection"""
    db_path = os.environ.get('DB_PATH', '/Users/d5/Documents/code/own/FPL-GPT/db/fpl.db')
    return sqlite3.connect(db_path)

def get_supabase_client():
    """Get Supabase client"""
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables are required")
    
    try:
        return create_client(supabase_url, supabase_key)
    except ImportError:
        raise ImportError("supabase is required. Install it with: pip install supabase")

def strict_deduplicate(batch_data, key_columns):
    """严格去重，确保同一批次内没有重复"""
    seen = set()
    unique_data = []
    duplicates_count = 0
    
    for item in batch_data:
        key = tuple(item[column] for column in key_columns)
        if key not in seen:
            seen.add(key)
            unique_data.append(item)
        else:
            duplicates_count += 1
            print(f"移除重复: {key}")
    
    if duplicates_count > 0:
        print(f"共移除 {duplicates_count} 条重复记录")
    
    return unique_data

def migrate_table(sqlite_conn, supabase, table_name, columns, batch_size=1000):
    """Migrate data from SQLite to Supabase for a specific table"""
    print(f"Migrating {table_name} table...")
    
    sqlite_cursor = sqlite_conn.cursor()
    
    # Get total count for progress tracking
    sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    total_rows = sqlite_cursor.fetchone()[0]
    print(f"  Total rows: {total_rows}")
    
    if total_rows == 0:
        print(f"  No data to migrate for {table_name}")
        return
    
    # Fetch data in batches
    offset = 0
    migrated_count = 0
    
    while offset < total_rows:
        sqlite_cursor.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (batch_size, offset))
        rows = sqlite_cursor.fetchall()
        # breakpoint()
        if not rows:
            break
        
        # Convert rows to dictionaries for Supabase
        data_to_insert = []
        for row in rows:
            row_dict = {}
            if table_name != "players" and table_name != "teams":
                # remove id column
                row = row[1:]
            else:
                # rename id to player_id or team_id
                columns[0] = 'player_id' if table_name == 'players' else 'team_id'
            for i, column in enumerate(columns):
                # Handle data type conversions if needed
                value = row[i]
                if column == 'kickoff_time' and value is not None:
                    # Handle different data types for kickoff_time
                    # Skip conversion for invalid values like "0"
                    value = datetime.fromisoformat(value).isoformat()

                row_dict[column] = value
            data_to_insert.append(row_dict)
        if table_name == "player_history":
            data_to_insert = strict_deduplicate(data_to_insert, ['player_id', 'round'])
        elif table_name == "predictions":
            data_to_insert = strict_deduplicate(data_to_insert, ['player_id', 'gw'])
        
        try:
            # Use upsert for tables with primary keys, insert for others
            if table_name == "player_history":
                result =supabase.table(table_name).upsert(data_to_insert, on_conflict="player_id,round").execute()
            elif table_name == "predictions":
                result = supabase.table(table_name).upsert(data_to_insert, on_conflict="player_id,gw").execute()
            else:
                result = supabase.table(table_name).upsert(data_to_insert).execute()
            
            migrated_count += len(rows)
            print(f"  Migrated {migrated_count}/{total_rows} rows")
        except Exception as e:
            print(f"  Error migrating batch: {e}")
            # Debug: print the first row's kickoff_time value and type
            
            print(f"    Error in row {offset + i + 1}: {e}")
            
            # 打印前几个字段的值来帮助调试
            # breakpoint()
            # sample_data = list(row_data.values())
            # print(f"    Sample data: {sample_data}")
        
        offset += batch_size
    
    print(f"  Completed: {migrated_count}/{total_rows} rows migrated")

def main():
    """Main migration function"""
    print("Starting migration from SQLite to Supabase...")
    print("Note: Make sure you have created the tables in Supabase first using supabase_schema.sql")
    
    try:
        sqlite_conn = get_sqlite_connection()
        supabase = get_supabase_client()
        
        # Debug: Check the actual Supabase table structure
        try:
            result = supabase.table("player_history").select("kickoff_time").limit(1).execute()
            print("Supabase player_history table exists and can be queried")
        except Exception as e:
            print(f"Warning: Could not query player_history table: {e}")
            print("Make sure the table exists with the correct schema")
            print("Run the supabase_schema.sql file to create the tables")
            return
        
        # Define table schemas for migration
        tables = {
            'teams': [
                'team_id', 'name', 'short_name'
            ],
            'players': [
                'player_id', 'web_name', 'first_name', 'second_name', 'team_id', 'team_code', 'element_type', 'now_cost', 'total_points', 'minutes', 'goals_scored', 'assists', 'clean_sheets', 'goals_conceded', 'own_goals', 'penalties_saved', 'penalties_missed', 'yellow_cards', 'red_cards', 'saves', 'bonus', 'bps', 'influence', 'creativity', 'threat', 'ict_index', 'event_points', 'chance_of_playing_next_round', 'chance_of_playing_this_round', 'status', 'news'
            ],
            
            'player_history': [
                'player_id', 'fixture_id', 'opponent_team_id', 'total_points', 'was_home', 'kickoff_time', 'round', 'minutes', 'goals_scored', 'assists', 'clean_sheets', 'goals_conceded', 'own_goals', 'penalties_saved', 'penalties_missed', 'yellow_cards', 'red_cards', 'saves', 'bonus', 'bps', 'influence', 'creativity', 'threat', 'ict_index'
            ],
            'predictions': [
                'player_id', 'gw', 'predicted_pts', 'opponent_team_id', 'is_home', 'difficulty'
            ]
        }
        
        # Migrate each table
        for table_name, columns in tables.items():
            migrate_table(sqlite_conn, supabase, table_name, columns)
        
        print("Migration completed successfully!")
        print("\nNext steps:")
        print("1. Update your .env files with SUPABASE_URL")
        print("2. Test the applications with: python fpl_data_loader/main.py")
        print("3. Test the MCP server with: python mcp_server/main.py")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        print("\nTroubleshooting tips:")
        print("1. Make sure Supabase tables are created (run supabase_schema.sql)")
        print("2. Check your SUPABASE_URL and SUPABASE_KEY environment variables")
        print("3. Install supabase: pip install supabase")
        raise
    finally:
        if 'sqlite_conn' in locals():
            sqlite_conn.close()

if __name__ == "__main__":
    main()