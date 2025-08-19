import aiohttp
import asyncio
import sqlite3
from datetime import datetime
import os
import argparse
from fpl import FPL
import dotenv
import requests

dotenv.load_dotenv()

# 获取数据库路径，优先使用环境变量
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
DB_NAME = os.environ.get('DB_PATH', os.path.join(PARENT_DIR, 'db', 'fpl.db'))
HUB_URL = "https://www.fantasyfootballhub.co.uk/player-data/player-data.json"

def create_tables(cursor):
    cursor.execute("DROP TABLE IF EXISTS players")
    cursor.execute("DROP TABLE IF EXISTS teams")
    cursor.execute("DROP TABLE IF EXISTS player_history")
    cursor.execute("DROP TABLE IF EXISTS predictions")
    
    # Create the teams table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY,
        name TEXT,
        short_name TEXT
    )
    ''')

    # Create the players table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        web_name TEXT,
        first_name TEXT,
        second_name TEXT,
        team_id INTEGER,
        team_code INTEGER,
        element_type INTEGER,  -- Position: 1: GK, 2: DEF, 3: MID, 4: FWD
        now_cost INTEGER,
        total_points INTEGER,
        minutes INTEGER,
        goals_scored INTEGER,
        assists INTEGER,
        clean_sheets INTEGER,
        goals_conceded INTEGER,
        own_goals INTEGER,
        penalties_saved INTEGER,
        penalties_missed INTEGER,
        yellow_cards INTEGER,
        red_cards INTEGER,
        saves INTEGER,
        bonus INTEGER,
        bps INTEGER,
        influence REAL,
        creativity REAL,
        threat REAL,
        ict_index REAL,
        event_points INTEGER,
        FOREIGN KEY (team_id) REFERENCES teams (id)
    )
    ''')

    # Create the player history table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS player_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER,
        fixture_id INTEGER,
        opponent_team_id INTEGER,
        total_points INTEGER,
        was_home BOOLEAN,
        kickoff_time TEXT,
        round INTEGER,
        minutes INTEGER,
        goals_scored INTEGER,
        assists INTEGER,
        clean_sheets INTEGER,
        goals_conceded INTEGER,
        own_goals INTEGER,
        penalties_saved INTEGER,
        penalties_missed INTEGER,
        yellow_cards INTEGER,
        red_cards INTEGER,
        saves INTEGER,
        bonus INTEGER,
        bps INTEGER,
        influence REAL,
        creativity REAL,
        threat REAL,
        ict_index REAL,
        FOREIGN KEY (player_id) REFERENCES players (id),
        FOREIGN KEY (opponent_team_id) REFERENCES teams (id)
    )
    ''')

    # Create the predictions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER,
        gw INTEGER,
        predicted_pts REAL,
        opponent_team_id INTEGER,
        is_home BOOLEAN,
        difficulty INTEGER,
        FOREIGN KEY (player_id) REFERENCES players (id),
        FOREIGN KEY (opponent_team_id) REFERENCES teams (id)
    )
    ''')

async def update_data():
    # Part 1: Fetch data from official FPL API
    print(f"[{datetime.now()}] Fetching data from FPL API...")
    fpl = FPL()
    await fpl.login_v2(os.getenv("FPL_EMAIL"), os.getenv("FPL_PASSWORD"))
    print(f"[{datetime.now()}] Logged in to FPL API")
    
    players = await fpl.get_players(include_summary=True)
    print(f"[{datetime.now()}] Fetched players from FPL API")
    teams_data = await fpl.get_teams()
    print(f"[{datetime.now()}] Fetched teams from FPL API")
    
    # 获取赛程信息
    fixtures = await fpl.get_fixtures()
    print(f"[{datetime.now()}] Fetched fixtures from FPL API")

    # Part 2: Connect to DB and insert FPL data
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 在更新前获取现有球员价格
    cursor.execute("SELECT id, now_cost, web_name FROM players")
    existing_players = {row[0]: {'cost': row[1], 'name': row[2]} for row in cursor.fetchall()}

    create_tables(cursor)

    price_changes = []

    for team in teams_data:
        cursor.execute("INSERT OR REPLACE INTO teams (id, name, short_name) VALUES (?, ?, ?)",
                       (team.id, team.name, team.short_name))

    for player in players:
        old_player_info = existing_players.get(player.id)
        if old_player_info and old_player_info['cost'] != player.now_cost:
            price_changes.append({
                'player_id': player.id,
                'web_name': player.web_name,
                'old_cost': old_player_info['cost'],
                'new_cost': player.now_cost
            })

        player_data = (
            player.id, player.web_name, player.first_name, player.second_name, player.team, player.team_code,
            player.element_type, player.now_cost, player.total_points, player.minutes, player.goals_scored, player.assists,
            player.clean_sheets, player.goals_conceded, player.own_goals, player.penalties_saved,
            player.penalties_missed, player.yellow_cards, player.red_cards, player.saves, player.bonus, player.bps,
            float(player.influence), float(player.creativity), float(player.threat), float(player.ict_index),
            player.event_points
        )
        cursor.execute('''
            INSERT OR REPLACE INTO players (
                id, web_name, first_name, second_name, team_id, team_code, element_type, now_cost, total_points, minutes,
                goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved,
                penalties_missed, yellow_cards, red_cards, saves, bonus, bps, influence, creativity,
                threat, ict_index, event_points
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', player_data)

        for history_item in player.history:
            history_data = (
                player.id, history_item['fixture'], history_item['opponent_team'], history_item['total_points'],
                history_item['was_home'], history_item['kickoff_time'], history_item['round'], history_item['minutes'],
                history_item['goals_scored'], history_item['assists'], history_item['clean_sheets'],
                history_item['goals_conceded'], history_item['own_goals'], history_item['penalties_saved'],
                history_item['penalties_missed'], history_item['yellow_cards'], history_item['red_cards'],
                history_item['saves'], history_item['bonus'], history_item['bps'], history_item['influence'],
                history_item['creativity'], history_item['threat'], history_item['ict_index']
            )
            cursor.execute('''
                INSERT INTO player_history (
                    player_id, fixture_id, opponent_team_id, total_points, was_home, kickoff_time, round,
                    minutes, goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved,
                    penalties_missed, yellow_cards, red_cards, saves, bonus, bps, influence, creativity,
                    threat, ict_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', history_data)
    
    # 创建轮次与赛程的映射
    fixtures_by_team_gw = {}
    for fixture in fixtures:
        if fixture.event is None:  # 跳过没有轮次信息的赛程
            continue
        
        gw = fixture.event
        team_h = fixture.team_h
        team_a = fixture.team_a
        
        # 为主队添加赛程信息
        if team_h not in fixtures_by_team_gw:
            fixtures_by_team_gw[team_h] = {}
        fixtures_by_team_gw[team_h][gw] = {
            'opponent_team_id': team_a,
            'is_home': True,
            'difficulty': fixture.team_h_difficulty
        }
        
        # 为客队添加赛程信息
        if team_a not in fixtures_by_team_gw:
            fixtures_by_team_gw[team_a] = {}
        fixtures_by_team_gw[team_a][gw] = {
            'opponent_team_id': team_h,
            'is_home': False,
            'difficulty': fixture.team_a_difficulty
        }
    
    # Part 3: Fetch and insert prediction data from Hub
    print(f"[{datetime.now()}] Fetching prediction data from Fantasy Football Hub...")
    try:
        response = requests.get(HUB_URL)
        response.raise_for_status()
        hub_players_data = response.json()
        print(f"[{datetime.now()}] Fetched prediction data.")

        for hub_player in hub_players_data:
            fpl_info = hub_player.get('fpl', {})
            player_id = fpl_info.get('id')
            if player_id:
                # 获取球员所属球队ID
                cursor.execute("SELECT team_id FROM players WHERE id = ?", (player_id,))
                result = cursor.fetchone()
                if not result:
                    continue
                
                team_id = result[0]
                team_fixtures = fixtures_by_team_gw.get(team_id, {})
                
                predictions = hub_player.get('data', {}).get('predictions', [])
                for prediction in predictions:
                    gw = prediction.get('gw')
                    predicted_pts = prediction.get('predicted_pts')
                    
                    # 获取该轮次的赛程信息
                    fixture_info = team_fixtures.get(gw, {})
                    opponent_team_id = fixture_info.get('opponent_team_id')
                    is_home = fixture_info.get('is_home')
                    difficulty = fixture_info.get('difficulty')
                    
                    # 插入预测数据，包括对阵和难度信息
                    cursor.execute("""
                        INSERT INTO predictions 
                        (player_id, gw, predicted_pts, opponent_team_id, is_home, difficulty) 
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (player_id, gw, predicted_pts, opponent_team_id, is_home, difficulty))
    except requests.exceptions.RequestException as e:
        print(f"Error fetching prediction data: {e}")

    conn.commit()
    conn.close()
    
    # 如果有价格变动，则发送 webhook
    # if price_changes:
    #     send_price_change_webhook(price_changes)
        
    print(f"[{datetime.now()}] Data has been successfully updated in {DB_NAME}")

def send_price_change_webhook(price_changes):
    webhook_url = "https://www.feishu.cn/flow/api/trigger-webhook/66148a80728f8b9b94ca8274015bfa93"
    if not webhook_url:
        print("PRICE_CHANGE_WEBHOOK_URL not set, skipping webhook.")
        return

    payloads = [{
        "text": "FPL Player Price Changes",
        "attachments": [
            {
                "color": "#36a64f",
                "pretext": f"{len(price_changes)} player(s) have price changes:",
                "fields": [
                    {
                        "title": f"{change['web_name']} ({change['player_id']})",
                        "value": f"Old: {change['old_cost']/10:.1f}M, New: {change['new_cost']/10:.1f}M",
                        "add": True if change['old_cost'] < change['new_cost'] else False
                    } 
                ],
                "footer": "FPL Price Change Notifier",
                "ts": int(datetime.now().timestamp())
            }
        ]
    } for change in price_changes] 
    
    for payload in payloads:
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            print(f"[{datetime.now()}] Successfully sent price change webhook.")
        except requests.exceptions.RequestException as e:
            print(f"Error sending price change webhook: {e}")

def query_player_by_name(name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM players WHERE web_name LIKE ?", (f'%{name}%',))
    players = cursor.fetchall()
    
    if not players:
        print(f"No players found with the name: {name}")
        return

    player_column_names = [description[0] for description in cursor.description]

    for player in players:
        print("-" * 40)
        player_id = player[0]
        for i, col_name in enumerate(player_column_names):
            print(f"{col_name}: {player[i]}")

        cursor.execute("SELECT h.round, t.short_name, h.was_home, h.total_points FROM player_history h JOIN teams t ON h.opponent_team_id = t.id WHERE h.player_id = ? ORDER BY h.round", (player_id,))
        history = cursor.fetchall()

        if history:
            print("\nMatch History (Actual Points):")
            print(f"{'Gameweek':<10} {'Opponent':<10} {'Venue':<10} {'Points':<10}")
            for gw, opponent, was_home, points in history:
                venue = "Home" if was_home else "Away"
                print(f"{gw:<10} {opponent:<10} {venue:<10} {points:<10}")

        cursor.execute("""
            SELECT p.gw, p.predicted_pts, t.short_name, p.is_home, p.difficulty 
            FROM predictions p 
            LEFT JOIN teams t ON p.opponent_team_id = t.id 
            WHERE p.player_id = ? 
            ORDER BY p.gw
        """, (player_id,))
        predictions = cursor.fetchall()

        if predictions:
            print("\nFuture Predictions (Predicted Points):")
            print(f"{'Gameweek':<10} {'Opponent':<10} {'Venue':<10} {'Difficulty':<10} {'Predicted Points':<20}")
            for gw, pts, opponent, is_home, difficulty in predictions:
                venue = "Home" if is_home else "Away"
                difficulty_str = str(difficulty) if difficulty is not None else "N/A"
                opponent_str = opponent if opponent is not None else "N/A"
                print(f"{gw:<10} {opponent_str:<10} {venue:<10} {difficulty_str:<10} {pts:.2f}")

    conn.close()

def main():
    parser = argparse.ArgumentParser(description="FPL Player Data Loader and Querier")
    parser.add_argument("--name", type=str, help="Query player by name")
    
    args = parser.parse_args()
    
    if args.name:
        query_player_by_name(args.name)
    else:
        asyncio.run(update_data())

if __name__ == "__main__":
    main()
