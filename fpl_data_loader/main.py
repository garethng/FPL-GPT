import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta, tzinfo
import os
import argparse
from fpl import FPL
import dotenv
import requests
from supabase import create_client, Client
from logpilot.log import Log

logger = Log.get_logger("fpl-data-loader")

dotenv.load_dotenv()

# 获取数据库连接，优先使用Supabase URL
HUB_URL = "https://www.fantasyfootballhub.co.uk/player-data/player-data.json"

beijing_time = datetime.now(tz=timezone(offset=timedelta(hours=8), name="Asia/Shanghai") ) 

def get_supabase_client():
    """Get Supabase client"""
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    
    if not supabase_url or not supabase_key:
        raise ValueError("Supabase credentials (SUPABASE_URL and SUPABASE_KEY) are required")
    
    return create_client(supabase_url, supabase_key)


async def update_data():
    # Part 1: Fetch data from official FPL API
    logger.info(f"[{datetime.now()}] Fetching data from FPL API...")
    fpl = FPL()
    # await fpl.login_v2(os.getenv("FPL_EMAIL"), os.getenv("FPL_PASSWORD"))
    fpl.access_token = None
    fpl.session = requests.Session()
    logger.info(f"[{datetime.now()}] Logged in to FPL API")
    players = await fpl.get_players(include_summary=True)
    logger.info(f"[{datetime.now()}] Fetched players from FPL API")
    teams_data = await fpl.get_teams()
    logger.info(f"[{datetime.now()}] Fetched teams from FPL API")
    
    # 获取赛程信息
    fixtures = await fpl.get_fixtures()
    logger.info(f"[{datetime.now()}] Fetched fixtures from FPL API")

    # Part 2: Connect to DB and insert FPL data
    supabase = get_supabase_client()
    
    logger.info(f"[{datetime.now()}] Using Supabase for data storage")
    
    # Upsert teams
    teams_to_upsert = [{"team_id": team.id, "name": team.name, "short_name": team.short_name} for team in teams_data]
    if teams_to_upsert:
        supabase.table("teams").upsert(teams_to_upsert).execute()
    
    # Get existing players for price change detection
    existing_players_result = supabase.table("players").select("player_id, now_cost, web_name, team_id").execute()
    existing_players = {player['player_id']: player for player in existing_players_result.data}
    
    # Get team names for price change notifications
    teams_result = supabase.table("teams").select("team_id, name").execute()
    team_map = {team['team_id']: team['name'] for team in teams_result.data}
    
    price_changes = []
    players_to_upsert = []
    history_to_insert = []
    
    for player in players:
        old_player_info = existing_players.get(player.id)
        if old_player_info and old_player_info['now_cost'] != player.now_cost:
            price_changes.append({
                'player_id': player.id,
                'web_name': player.web_name,
                'team_name': team_map.get(old_player_info['team_id'], 'Unknown'),
                'old_cost': old_player_info['now_cost'],
                'new_cost': player.now_cost
            })

        # Prepare player data
        player_data = {
            "player_id": player.id,
            "web_name": player.web_name,
            "first_name": player.first_name,
            "second_name": player.second_name,
            "team_id": player.team,
            "team_code": player.team_code,
            "element_type": player.element_type,
            "now_cost": player.now_cost,
            "total_points": player.total_points,
            "minutes": player.minutes,
            "goals_scored": player.goals_scored,
            "assists": player.assists,
            "clean_sheets": player.clean_sheets,
            "goals_conceded": player.goals_conceded,
            "own_goals": player.own_goals,
            "penalties_saved": player.penalties_saved,
            "penalties_missed": player.penalties_missed,
            "yellow_cards": player.yellow_cards,
            "red_cards": player.red_cards,
            "saves": player.saves,
            "bonus": player.bonus,
            "bps": player.bps,
            "influence": float(player.influence),
            "creativity": float(player.creativity),
            "threat": float(player.threat),
            "ict_index": float(player.ict_index),
            "event_points": player.event_points,
            "chance_of_playing_next_round": getattr(player, 'chance_of_playing_next_round', None),
            "chance_of_playing_this_round": getattr(player, 'chance_of_playing_this_round', None),
            "status": getattr(player, 'status', None),
            "news": getattr(player, 'news', None),
            "ownership_percentage": float(player.selected_by_percent) if hasattr(player, 'selected_by_percent') else 0.0
        }
        players_to_upsert.append(player_data)

        # Prepare history data
        if hasattr(player, 'history'):
            for history_item in player.history:
                history_data = {
                    "player_id": player.id,
                    "fixture_id": history_item['fixture'],
                    "opponent_team_id": history_item['opponent_team'],
                    "total_points": history_item['total_points'],
                    "was_home": history_item['was_home'],
                    "kickoff_time": history_item['kickoff_time'],
                    "round": history_item['round'],
                    "minutes": history_item['minutes'],
                    "goals_scored": history_item['goals_scored'],
                    "assists": history_item['assists'],
                    "clean_sheets": history_item['clean_sheets'],
                    "goals_conceded": history_item['goals_conceded'],
                    "own_goals": history_item['own_goals'],
                    "penalties_saved": history_item['penalties_saved'],
                    "penalties_missed": history_item['penalties_missed'],
                    "yellow_cards": history_item['yellow_cards'],
                    "red_cards": history_item['red_cards'],
                    "saves": history_item['saves'],
                    "bonus": history_item['bonus'],
                    "bps": history_item['bps'],
                    "influence": history_item['influence'],
                    "creativity": history_item['creativity'],
                    "threat": history_item['threat'],
                    "ict_index": history_item['ict_index']
                }
                history_to_insert.append(history_data)

    # Upsert players in batches
    if players_to_upsert:
        supabase.table("players").upsert(players_to_upsert).execute()
    
    # Merge DGW (Double Gameweek) data - sum up stats for same player in same round
    from collections import defaultdict
    history_merged = defaultdict(lambda: {
        "player_id": None, "fixture_id": None, "opponent_team_id": None,
        "total_points": 0, "was_home": None, "kickoff_time": None, "round": None,
        "minutes": 0, "goals_scored": 0, "assists": 0, "clean_sheets": 0,
        "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0,
        "penalties_missed": 0, "yellow_cards": 0, "red_cards": 0,
        "saves": 0, "bonus": 0, "bps": 0, "influence": 0.0,
        "creativity": 0.0, "threat": 0.0, "ict_index": 0.0
    })
    
    for h in history_to_insert:
        key = (h['player_id'], h['round'])
        m = history_merged[key]
        # Set base fields (keep first occurrence)
        if m["player_id"] is None:
            m["player_id"] = h["player_id"]
            m["round"] = h["round"]
            m["fixture_id"] = h["fixture_id"]
            m["opponent_team_id"] = h["opponent_team_id"]
            m["was_home"] = h["was_home"]
            m["kickoff_time"] = h["kickoff_time"]
        # Sum up stats
        m["total_points"] += h["total_points"]
        m["minutes"] += h["minutes"]
        m["goals_scored"] += h["goals_scored"]
        m["assists"] += h["assists"]
        m["clean_sheets"] += h["clean_sheets"]
        m["goals_conceded"] += h["goals_conceded"]
        m["own_goals"] += h["own_goals"]
        m["penalties_saved"] += h["penalties_saved"]
        m["penalties_missed"] += h["penalties_missed"]
        m["yellow_cards"] += h["yellow_cards"]
        m["red_cards"] += h["red_cards"]
        m["saves"] += h["saves"]
        m["bonus"] += h["bonus"]
        m["bps"] += h["bps"]
        m["influence"] += float(h["influence"])
        m["creativity"] += float(h["creativity"])
        m["threat"] += float(h["threat"])
        m["ict_index"] += float(h["ict_index"])
    
    history_to_insert = list(history_merged.values())
    logger.info(f"[{datetime.now()}] Merged DGW data: {len(history_to_insert)} records after merging")
    
    # Insert history in batches
    if history_to_insert:
        supabase.table("player_history").upsert(history_to_insert, on_conflict="player_id,round").execute()
    
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
    
    # Part 3: Generate predictions using internal model
    logger.info(f"[{datetime.now()}] Generating predictions using internal model (my_predictor)...")
    try:
        from my_predictor import Predictor
        predictor = Predictor(supabase_client=supabase)
        predictions = predictor.generate_predictions()
        
        if predictions:
            logger.info(f"[{datetime.now()}] Saving {len(predictions)} predictions to Supabase...")
            # Upsert in batches of 1000
            batch_size = 1000
            for i in range(0, len(predictions), batch_size):
                batch = predictions[i:i + batch_size]
                supabase.table("predictions").upsert(batch, on_conflict="player_id,gw").execute()
            logger.info(f"[{datetime.now()}] Predictions saved.")
        else:
            logger.info("No predictions generated.")

    except Exception as e:
        logger.error(f"Error generating/saving internal predictions: {e}")
        import traceback
        traceback.print_exc()

    # Part 3b: (Optional) Fetch prediction data from Hub (Legacy/Backup)
    # logger.info(f"[{datetime.now()}] Fetching prediction data from Fantasy Football Hub...")
    # try:
    #     response = requests.get(HUB_URL)
    # ... (commented out legacy hub code)
    
    logger.info(f"[{datetime.now()}] Data has been successfully updated to Supabase")
    
    # 如果有价格变动，则发送 webhook
    if price_changes:
        send_price_change_webhook(price_changes)
        
    logger.info(f"[{datetime.now()}] Data has been successfully updated")

def send_price_change_webhook(price_changes):
    webhook_url = "https://www.feishu.cn/flow/api/trigger-webhook/66148a80728f8b9b94ca8274015bfa93"
    notice_webhook_url = "https://www.feishu.cn/flow/api/trigger-webhook/c6aabf0ef33c336a4f8c1799d0c79ce6"
    if not webhook_url:
        logger.info("PRICE_CHANGE_WEBHOOK_URL not set, skipping webhook.")
        return

    payloads = [{
        "text": "FPL Player Price Changes",
        "attachments": [
            {
                "color": "#36a64f",
                "pretext": f"{len(price_changes)} player(s) have price changes:",
                "fields": 
                    {
                        "title": f"{change['web_name']}",
                        "team": f"{change['team_name']}",
                        "playid": f"{change['player_id']}",
                        "old_value": f"{change['old_cost']/10:.1f}M",
                        "new_value": f"{change['new_cost']/10:.1f}M",
                        "add":  "升值" if change['old_cost'] < change['new_cost'] else "贬值"
                    } 
                ,
                "footer": "FPL Price Change Notifier",
                "ts": int(datetime.now().timestamp())
            }
        ]
    } for change in price_changes] 
    up_notice_str = ""
    down_notice_str = ""
    for change in price_changes:
        if change['old_cost'] < change['new_cost']:
            up_notice_str += f"🔺 {change['web_name']} {change['team_name']}  {change['old_cost']/10:.1f}M -> {change['new_cost']/10:.1f}M\n"
        else:
            down_notice_str += f"🟢 {change['web_name']} {change['team_name']}  {change['old_cost']/10:.1f}M -> {change['new_cost']/10:.1f}M\n"
    
    up_payload = {
        "title": "FPL Player Price Risers 📈",
        "text": up_notice_str
    }
    down_payload = {
        "title": "FPL Player Price Fallers 📉",
        "text": down_notice_str
    }

    try:
        response = requests.post(notice_webhook_url, json=up_payload)
        response.raise_for_status()
        response = requests.post(notice_webhook_url, json=down_payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.info(f"Error sending price change webhook: {e}")

    for payload in payloads:
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            logger.info(f"[{datetime.now()}] Successfully sent price change webhook.")
        except requests.exceptions.RequestException as e:
            logger.info(f"Error sending price change webhook: {e}")

def query_player_by_name(name):
    supabase = get_supabase_client()
    
    # Use Supabase
    players_result = supabase.table("players").select("*").ilike("web_name", f"%{name}%").execute()
    players = players_result.data
    
    if not players:
        logger.info(f"No players found with the name: {name}")
        return

    for player in players:
        logger.info("-" * 40)
        player_id = player['player_id']
        for key, value in player.items():
            logger.info(f"{key}: {value}")

        # Get player history
        history_result = supabase.table("player_history").select("round, opponent_team_id, was_home, total_points").eq("player_id", player_id).order("round").execute()
        history = history_result.data
        
        if history:
            logger.info("\nMatch History (Actual Points):")
            logger.info(f"{'Gameweek':<10} {'Opponent':<10} {'Venue':<10} {'Points':<10}")
            for item in history:
                # Get opponent team name
                team_result = supabase.table("teams").select("short_name").eq("team_id", item['opponent_team_id']).execute()
                opponent = team_result.data[0]['short_name'] if team_result.data else "Unknown"
                
                venue = "Home" if item['was_home'] else "Away"
                logger.info(f"{item['round']:<10} {opponent:<10} {venue:<10} {item['total_points']:<10}")

        # Get predictions
        predictions_result = supabase.table("predictions").select("gw, predicted_pts, opponent_team_id, is_home, difficulty").eq("player_id", player_id).order("gw").execute()
        predictions = predictions_result.data
        
        if predictions:
            logger.info("\nFuture Predictions (Predicted Points):")
            logger.info(f"{'Gameweek':<10} {'Opponent':<10} {'Venue':<10} {'Difficulty':<10} {'Predicted Points':<20}")
            for pred in predictions:
                # Get opponent team name
                team_result = supabase.table("teams").select("short_name").eq("team_id", pred['opponent_team_id']).execute()
                opponent = team_result.data[0]['short_name'] if team_result.data else "N/A"
                
                venue = "Home" if pred['is_home'] else "Away"
                difficulty_str = str(pred['difficulty']) if pred['difficulty'] is not None else "N/A"
                logger.info(f"{pred['gw']:<10} {opponent:<10} {venue:<10} {difficulty_str:<10} {pred['predicted_pts']:.2f}")

async def get_classic_league_standings(league_id):
    """Get the latest league standings and send to webhook"""
    try:
        response = requests.get(f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/")
        league = response.json()
        standings = league['standings']['results']
        
        # Prepare webhook payload
        # webhook_url = os.environ.get('LEAGUE_WEBHOOK_URL')
        # if not webhook_url:
        #     logger.info("LEAGUE_WEBHOOK_URL not set, skipping webhook.")
        #     return
        
        stand_str = [f"#{i+1}: {entry['entry_name']} - {entry['total']} points - {entry['total']-standings[0]['total']}" for i, entry in enumerate(standings)]
        stand_str = "\n".join(stand_str)
        logger.info(f"Standings: {stand_str}")
        
        payload = {
            "title": f"FPL League {league['league']['name']} Standings - {beijing_time.strftime('%Y-%m-%d')}",
            "text": stand_str
        }
        
        # Send to webhook
        webhook_url = "https://www.feishu.cn/flow/api/trigger-webhook/0c73dac154c1da3a6af7d08607fb9c34"
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        logger.info(f"Successfully sent league {league_id} standings to webhook")
        print(payload)
        return standings
        
    except Exception as e:
        logger.info(f"Error getting league standings: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_h2h_league_standings(league_id):
    data = fetch_all_h2h_league_matches(league_id)
    result = parse_h2h_league(data)
    webhook_url = "https://www.feishu.cn/flow/api/trigger-webhook/0c73dac154c1da3a6af7d08607fb9c34"
    payload = {
        "title": f"FPL League 晴雪杯 Standings - {beijing_time.strftime('%Y-%m-%d')}",
        "text": result
    }
    response = requests.post(webhook_url, json=payload)
    response.raise_for_status()
    return result

def fetch_all_h2h_league_matches(league_id):
    base_url = f"https://fantasy.premierleague.com/api/leagues-h2h-matches/league/{league_id}/"
    page = 1
    has_next = True
    merged_data = None
    all_results = []

    while has_next:
        response = requests.get(base_url, params={"page": page})
        response.raise_for_status()
        page_data = response.json()

        if merged_data is None:
            merged_data = page_data

        all_results.extend(page_data.get("results", []))
        has_next = page_data.get("has_next", False)
        logger.info(
            f"Fetched H2H league {league_id} page={page}, results={len(page_data.get('results', []))}, has_next={has_next}"
        )
        page += 1

    if merged_data is None:
        return {"results": []}

    merged_data["results"] = all_results
    merged_data["has_next"] = False
    merged_data["page"] = 1
    return merged_data

def parse_h2h_league(data):
    def init_team(match, side):
        return {
            'name': match[f'entry_{side}_name'],
            'player': match[f'entry_{side}_player_name'],
            'played': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'points_for': 0,
            'points_against': 0,
            'total_points': 0
        }

    teams = {}
    played_matches = []

    for match in data.get('results', []):
        # Only process matches that have been played (points > 0)
        if not (match['entry_1_points'] > 0 or match['entry_2_points'] > 0):
            continue

        played_matches.append(match)

        team1_id = match['entry_1_entry']
        team2_id = match['entry_2_entry']
        teams.setdefault(team1_id, init_team(match, 1))
        teams.setdefault(team2_id, init_team(match, 2))

        teams[team1_id]['played'] += 1
        teams[team1_id]['points_for'] += match['entry_1_points']
        teams[team1_id]['points_against'] += match['entry_2_points']
        teams[team1_id]['wins'] += match['entry_1_win']
        teams[team1_id]['draws'] += match['entry_1_draw']
        teams[team1_id]['losses'] += match['entry_1_loss']
        teams[team1_id]['total_points'] += match['entry_1_total']

        teams[team2_id]['played'] += 1
        teams[team2_id]['points_for'] += match['entry_2_points']
        teams[team2_id]['points_against'] += match['entry_1_points']
        teams[team2_id]['wins'] += match['entry_2_win']
        teams[team2_id]['draws'] += match['entry_2_draw']
        teams[team2_id]['losses'] += match['entry_2_loss']
        teams[team2_id]['total_points'] += match['entry_2_total']

    standings = []
    for _, stats in teams.items():
        stats['points_diff'] = stats['points_for'] - stats['points_against']
        standings.append(stats)

    standings.sort(key=lambda x: (-x['total_points'], -x['points_diff']))

    str_standings = ""
    str_standings += "Current Standings\n"
    str_standings += "-" * 10 + "\n"
    str_standings += f"{'#':<3} {'Team':<15} {'W':<3} {'D':<3} {'L':<3} {'Pts':<4}\n"
    str_standings += "-" * 10 + "\n"
    for i, team in enumerate(standings, 1):
        # remove blank space in team name
        str_standings += f"{i:<3} {team['name'].strip():<15} "
        str_standings += f"{team['wins']:<3} {team['draws']:<3} {team['losses']:<3} "
        str_standings += f"{team['total_points']:<4}\n"
    str_standings += "-" * 10 + "\n"
    str_standings += "\n"
    str_standings += "Current gameweek:\n"
    str_standings += "-" * 10 + "\n"

    event = -1
    for match in reversed(played_matches):
        if event == -1:
            event = match['event']

        if match['event'] != event:
            break

        winner = "Draw"
        if match['entry_1_win']:
            winner = match['entry_1_name']
        elif match['entry_2_win']:
            winner = match['entry_2_name']

        str_standings += f"{match['entry_1_name']} {match['entry_1_points']} - {match['entry_2_points']} {match['entry_2_name']}\n"
        str_standings += f"  Winner: {winner}\n"
        str_standings += f"  Event: {match['event']}\n"
        str_standings += "\n"
    # format str_standings to display beautiful
    str_standings = str_standings.replace("\n", "\n\n")
    str_standings = str_standings.replace("  ", " ")
    str_standings = str_standings.replace("  ", " ")
    print(str_standings)
    return str_standings

def main():
    parser = argparse.ArgumentParser(description="FPL Player Data Loader and Querier")
    parser.add_argument("--name", type=str, help="Query player by name")
    parser.add_argument("--c_league", type=int, help="Get league standings and send to webhook")
    parser.add_argument("--h2h_league", type=int, help="Get league standings and send to webhook")
    args = parser.parse_args()
    
    if args.name:
        query_player_by_name(args.name)
    elif args.c_league:
        asyncio.run(get_classic_league_standings(args.c_league))
    elif args.h2h_league:
        get_h2h_league_standings(args.h2h_league)
    else:
        asyncio.run(update_data())

if __name__ == "__main__":
    main()
