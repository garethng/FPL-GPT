import asyncio
import os
import json
import pulp
from collections import defaultdict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Constants ---
# Script is in fpl_dashboard/, docs is in root/docs/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, 'docs')
DATA_FILE = os.path.join(DOCS_DIR, 'data.json')

# 2025/26 Scoring Constants
PTS_MINS_60 = 2
PTS_MINS_1 = 1
PTS_GOAL_FWD = 4
PTS_GOAL_MID = 5
PTS_GOAL_DEF = 6
PTS_GOAL_GK = 10
PTS_ASSIST = 3
PTS_CS_DEF = 4
PTS_CS_MID = 1
PTS_CS_GK = 4
PTS_SAVES_3 = 1
PTS_YEL = -1
PTS_RED = -3
PTS_OWN_GOAL = -2
PTS_GC_2_DEF = -1

DIFFICULTY_STEP = 0.1
HOME_ADVANTAGE = 1.1

import sys
sys.path.append(os.path.join(BASE_DIR, 'fpl_data_loader'))

try:
    from my_predictor import Predictor
except ImportError as e:
    # Fallback if path issue
    print(f"Could not import Predictor: {e}. Breakdown will be unavailable.")
    Predictor = None

class FPLManager:
    def __init__(self):
        self.supabase_url = os.environ.get("SUPABASE_URL")
        self.supabase_key = os.environ.get("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            # Fallback for GH Actions if not set (though they should be)
            print("Warning: Supabase credentials missing.")
        
        self.supabase = create_client(self.supabase_url, self.supabase_key)
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

        # Initialize Predictor for breakdown
        if Predictor:
            self.predictor = Predictor(self.supabase)
        else:
            self.predictor = None

    def enrich_with_breakdown(self, team):
        """Adds detailed points breakdown to the selected team."""
        if not self.predictor: return team
        
        print("Enriching team with detailed points breakdown...")
        # Ensure predictor has data
        # We assume fetch_data() loads everything needed.
        # Check if loaded? Predictor logic checks internally or we call it.
        # But my_predictor.fetch_data() is what loads history.
        # get_points_breakdown calls fetch_data/train if needed.
        
        for p in team:
            try:
                bd = self.predictor.get_points_breakdown(p['player_id'])
                if bd:
                    p['breakdown'] = bd['breakdown']
            except Exception as e:
                print(f"Error getting breakdown for {p['web_name']}: {e}")
        
        return team

    def get_fpl_status(self):
        resp = self.session.get("https://fantasy.premierleague.com/api/bootstrap-static/")
        data = resp.json()
        events = data['events']
        
        current_gw = next((e for e in events if e['is_current']), None)
        next_gw = next((e for e in events if e['is_next']), None)
        
        return current_gw, next_gw

    def fetch_live_points(self, gw_id):
        print(f"Fetching live points for GW {gw_id}...")
        resp = self.session.get(f"https://fantasy.premierleague.com/api/event/{gw_id}/live/")
        data = resp.json()
        # Map element_id -> stats
        live_data = {}
        for el in data['elements']:
            live_data[el['id']] = el['stats']['total_points']
        return live_data

    def fetch_predictions(self, next_gw_id):
        print(f"Fetching pre-calculated predictions for GW {next_gw_id} from Supabase...")
        
        # 1. Fetch Teams (Mapping)
        teams_map = {} # id -> short_name
        res = self.supabase.table("teams").select("*").execute()
        for t in res.data:
            teams_map[t['team_id']] = t['short_name'] if t['short_name'] else t['name'][:3].upper()

        # 2. Fetch Players
        players = {}
        res = self.supabase.table("players").select("*").execute()
        for p in res.data:
            players[p['player_id']] = p
            
        # 3. Fetch Predictions from Supabase
        res = self.supabase.table("predictions").select("*").eq("gw", next_gw_id).execute()
        db_preds = res.data
        
        if not db_preds:
            print(f"No pre-calculated predictions found for GW {next_gw_id} in Supabase.")
            return []

        predictions = []
        for dp in db_preds:
            pid = dp['player_id']
            if pid not in players: continue
            
            p = players[pid].copy()
            # Basic Availability Check
            if p['status'] in ['u', 'i', 'n']: continue
            
            p['predicted_points'] = dp['predicted_pts']
            p['is_home'] = dp['is_home']
            p['opponent_difficulty'] = dp['difficulty']
            
            # Add Team Info
            p['team_short_name'] = teams_map.get(p['team_id'], "UNK")
            p['opponent_short_name'] = teams_map.get(dp['opponent_team_id'], "UNK")
            
            predictions.append(p)
            
        print(f"Found {len(predictions)} valid predictions for dashboard.")
        return predictions

    def optimize_team(self, predictions, budget=100.0):
        print("Optimizing team...")
        # Prepare data for Pulp
        players = predictions
        
        # Filter low predictions to speed up
        players = [p for p in players if p['predicted_points'] > 0]
        
        player_ids = [p['player_id'] for p in players]
        points = {p['player_id']: p['predicted_points'] for p in players}
        costs = {p['player_id']: p['now_cost'] / 10.0 for p in players}
        teams = {p['player_id']: p['team_id'] for p in players}
        positions = {p['player_id']: p['element_type'] for p in players}
        
        # Variables: x[i] = 1 if player i is selected
        x = pulp.LpVariable.dicts("player", player_ids, cat="Binary")
        
        # Problem: Maximize Total Points
        prob = pulp.LpProblem("FPL_Team_Selection", pulp.LpMaximize)
        prob += pulp.lpSum([points[i] * x[i] for i in player_ids])
        
        # Constraints
        # 1. Squad Size = 15
        prob += pulp.lpSum([x[i] for i in player_ids]) == 15
        
        # 2. Budget <= 100
        prob += pulp.lpSum([costs[i] * x[i] for i in player_ids]) <= budget
        
        # 3. Position Constraints
        # GK = 2
        prob += pulp.lpSum([x[i] for i in player_ids if positions[i] == 1]) == 2
        # DEF = 5
        prob += pulp.lpSum([x[i] for i in player_ids if positions[i] == 2]) == 5
        # MID = 5
        prob += pulp.lpSum([x[i] for i in player_ids if positions[i] == 3]) == 5
        # FWD = 3
        prob += pulp.lpSum([x[i] for i in player_ids if positions[i] == 4]) == 3
        
        # 4. Team Constraints (Max 3 per team)
        all_teams = set(teams.values())
        for t in all_teams:
            prob += pulp.lpSum([x[i] for i in player_ids if teams[i] == t]) <= 3
            
        # Solve
        # Suppress output
        prob.solve(pulp.PULP_CBC_CMD(msg=0))
        
        if pulp.LpStatus[prob.status] != "Optimal":
            print("Optimization failed to find optimal solution.")
            return []
            
        selected_ids = [i for i in player_ids if x[i].varValue == 1.0]
        selected_players = [p for p in players if p['player_id'] in selected_ids]
        
        # Now Pick Starting XI
        # Standard formation rules: 1 GK, min 3 DEF, min 1 FWD.
        # Heuristic: Pick Top scoring GK, Top scoring outfield players fitting formation
        
        # Sort by points desc
        selected_players.sort(key=lambda x: x['predicted_points'], reverse=True)
        
        starters = []
        bench = []
        
        # 1. Pick GK
        gks = [p for p in selected_players if p['element_type'] == 1]
        starters.append(gks[0])
        bench.append(gks[1])
        
        # 2. Pick Outfield
        outfield = [p for p in selected_players if p['element_type'] != 1]
        # We need 10 outfield.
        # Constraints: Min 3 DEF, Min 1 FWD. (MID usually no min > 0 implied)
        
        # Greedy approach for Starting XI:
        # Take top 10 outfielders, check validity. If not valid, swap.
        # Actually simpler: Optimization step 2? Or just sorting.
        # Given we optimized for Total Points, the top 11 (1 GK + 10 outfield) usually works 
        # unless we have e.g. 5 Mids + 3 Fwds + 2 Defs (invalid).
        
        # Let's try to pick best valid formation
        defs = [p for p in outfield if p['element_type'] == 2]
        mids = [p for p in outfield if p['element_type'] == 3]
        fwds = [p for p in outfield if p['element_type'] == 4]
        
        # Must have 3 DEF, 1 FWD.
        # Current squad has 5 DEF, 5 MID, 3 FWD.
        # We need to drop 4 outfielders to bench.
        # Worst casebench: 2 DEF (leaves 3), 2 FWD (leaves 1). This is valid.
        # So as long as we don't bench >2 DEFs or >2 FWDs, we are fine.
        
        # Strategy: Mark all as starters, then move lowest pointers to bench until 11 remain, respecting constraints.
        
        # Start with all outfield as 'potential starters'
        current_starters = outfield[:]
        current_bench = []
        
        # We have 13 outfield players (5+5+3). We need 10 starters.
        # So we need to remove 3 players to bench.
        # Sort by points ascending (lowest first) to try and bench them
        current_starters.sort(key=lambda x: x['predicted_points'])
        
        removed_count = 0
        i = 0
        while removed_count < 3 and i < len(current_starters):
            cand = current_starters[i]
            # Can we remove cand?
            c_defs = len([p for p in current_starters if p['element_type'] == 2 and p != cand])
            c_fwds = len([p for p in current_starters if p['element_type'] == 4 and p != cand])
            
            if c_defs >= 3 and c_fwds >= 1:
                # Safe to remove
                current_bench.append(cand)
                current_starters.pop(i)
                removed_count += 1
                # Don't increment i, as list shifted
            else:
                i += 1
        
        starters.extend(current_starters)
        bench.extend(current_bench)
        
        # Captaincy
        starters.sort(key=lambda x: x['predicted_points'], reverse=True)
        captain = starters[0]
        vice = starters[1]
        
        # Final Format
        final_team = []
        for p in starters:
            p['role'] = 'Starter'
            p['is_captain'] = (p == captain)
            p['is_vice'] = (p == vice)
            final_team.append(p)
            
        for p in bench:
            p['role'] = 'Bench'
            p['is_captain'] = False
            p['is_vice'] = False
            final_team.append(p)
            
        return final_team

    def run(self):
        curr, next_gw = self.get_fpl_status()
        
        # Load existing data
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                saved_data = json.load(f)
        else:
            saved_data = None
            
        # Decision Logic
        if curr and not curr['finished']:
            print(f"Gameweek {curr['id']} is LIVE.")
            # Mode: Live Update
            # If we have a saved team for this GW, update it.
            if saved_data and saved_data['gameweek'] == curr['id']:
                live_pts = self.fetch_live_points(curr['id'])
                total_live = 0
                for p in saved_data['team']:
                    lp = live_pts.get(p['player_id'], 0)
                    p['live_points'] = lp
                    # Captain logic for total
                    mult = 2 if p['is_captain'] else 1
                    if p['role'] == 'Starter':
                         total_live += lp * mult
                    # Auto-sub logic is too complex for this script, ignoring for now.
                
                saved_data['summary']['total_live'] = total_live
                saved_data['status'] = 'live'
                
                with open(DATA_FILE, 'w') as f:
                    json.dump(saved_data, f, indent=2)
                print("Updated live scores.")
                
            else:
                print("No saved team for current GW. Skipping or Generating late (Skipping per instructions).")
                
        elif next_gw:
            print(f"Preparing for Gameweek {next_gw['id']}...")
            # Mode: Prediction
            
            # Check if we already have prediction for this GW
            if saved_data and saved_data['gameweek'] == next_gw['id'] and saved_data['status'] == 'prediction':
                print("Prediction for next GW already exists. Skipping.")
                return

            preds = self.fetch_predictions(next_gw['id'])
            team = self.optimize_team(preds)
            
            if not team:
                print("Failed to optimize team.")
                return
            
            # Enrich with breakdown
            team = self.enrich_with_breakdown(team)
                
            total_pred = sum(p['predicted_points'] * (2 if p['is_captain'] else 1) for p in team if p['role'] == 'Starter')
            
            output = {
                'status': 'prediction',
                'gameweek': next_gw['id'],
                'team': team,
                'summary': {
                    'total_predicted': total_pred,
                    'total_live': 0
                }
            }
            
            with open(DATA_FILE, 'w') as f:
                json.dump(output, f, indent=2)
            print("Generated new prediction.")
            
        else:
            print("Season finished or unknown state.")

if __name__ == "__main__":
    mgr = FPLManager()
    mgr.run()
