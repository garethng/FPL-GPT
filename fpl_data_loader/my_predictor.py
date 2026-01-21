import os
import requests
from collections import defaultdict
from supabase import create_client
from fpl import FPL
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

# Suppress warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
WEIGHT_RECENT = 1.0 
DIFFICULTY_STEP = 0.1 
HOME_ADVANTAGE = 1.1

# FPL Scoring Constants (2025/26)
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

def get_position_name(element_type):
    return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(element_type, "UNK")

class Predictor:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        if not self.supabase:
            self.supabase_url = os.environ.get("SUPABASE_URL")
            self.supabase_key = os.environ.get("SUPABASE_KEY")
            if not self.supabase_url or not self.supabase_key:
                raise ValueError("Supabase credentials missing")
            self.supabase = create_client(self.supabase_url, self.supabase_key)
        
        self.fpl_session = requests.Session()
        self.fpl_session.verify = False
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.fpl_session.mount('https://', HTTPAdapter(max_retries=retries))

        self.global_ratios = {} # Store conversion rates by position
        self.players = {}
        self.history = defaultdict(list)
        self.team_fixtures = {}
        self.next_gw = None

    def fetch_data(self):
        print("Fetching data for prediction...")
        # 1. Fetch all players
        res = self.supabase.table("players").select("player_id, web_name, element_type, team_id, status, now_cost").execute()
        for p in res.data:
            self.players[p['player_id']] = p

        # 2. Fetch History (All history for training)
        print("Fetching full player history...")
        limit = 1000
        offset = 0
        all_history = []
        while True:
            res = self.supabase.table("player_history").select("*").range(offset, offset + limit - 1).order("round", desc=True).execute()
            batch = res.data
            all_history.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            
        print(f"Total history records: {len(all_history)}")

        for h in all_history:
            self.history[h['player_id']].append(h)

        # 3. Get Next GW Fixtures from FPL API
        # Use sync request
        try:
            resp = self.fpl_session.get("https://fantasy.premierleague.com/api/fixtures/?future=1")
            resp.raise_for_status()
            fixtures = resp.json()
            
            if not fixtures:
                print("No future fixtures found.")
                return None
            
            self.next_gw = fixtures[0]['event']
            print(f"Next Gameweek: {self.next_gw}")
            
            gw_fixtures = [f for f in fixtures if f['event'] == self.next_gw]
            
            for f in gw_fixtures:
                home = f['team_h']
                away = f['team_a']
                diff_h = f['team_h_difficulty']
                diff_a = f['team_a_difficulty']
                self.team_fixtures[home] = {'opponent': away, 'difficulty': diff_h, 'is_home': True}
                self.team_fixtures[away] = {'opponent': home, 'difficulty': diff_a, 'is_home': False}

            return self.next_gw
        except Exception as e:
            print(f"Error fetching fixtures: {e}")
            return None

    def train_global_ratios(self):
        """
        Calculates conversion rates (Threat -> Goals, Creativity -> Assists) per position 
        using the entire history dataset.
        """
        print("Training model on historical data...")
        stats = {
            1: {'goals': 0, 'threat': 0, 'assists': 0, 'creativity': 0},
            2: {'goals': 0, 'threat': 0, 'assists': 0, 'creativity': 0},
            3: {'goals': 0, 'threat': 0, 'assists': 0, 'creativity': 0},
            4: {'goals': 0, 'threat': 0, 'assists': 0, 'creativity': 0},
        }
        
        for pid, games in self.history.items():
            player = self.players.get(pid)
            if not player: continue
            pos = player['element_type']
            
            for g in games:
                stats[pos]['goals'] += g['goals_scored']
                stats[pos]['threat'] += float(g['threat'])
                stats[pos]['assists'] += g['assists']
                stats[pos]['creativity'] += float(g['creativity'])
        
        # Calculate Ratios
        for pos, s in stats.items():
            t_ratio = s['goals'] / s['threat'] if s['threat'] > 0 else 0
            c_ratio = s['assists'] / s['creativity'] if s['creativity'] > 0 else 0
            self.global_ratios[pos] = {
                'threat_to_goal': t_ratio,
                'creativity_to_assist': c_ratio
            }
            # print(f"Pos {get_position_name(pos)}: T2G={t_ratio:.4f}, C2A={c_ratio:.4f}")


    def calculate_expected_stats(self, player_id):
        player = self.players.get(player_id)
        if not player: return None
        
        # Availability Check
        if player['status'] in ['u', 'i', 'n']: 
            return None 

        history = self.history.get(player_id, [])
        history.sort(key=lambda x: x['round'], reverse=True)
        
        # Last N games where played
        played_games = [h for h in history if h['minutes'] > 0]
        recent_games = played_games[:5]
        
        if not recent_games:
            return None

        # Weighted Average (More recent = higher weight)
        total_weight = 0
        w_threat = 0
        w_creativity = 0
        w_mins = 0
        w_saves = 0
        w_bonus = 0
        w_yel = 0
        w_conceded = 0
        w_cs = 0 
        
        for i, g in enumerate(recent_games):
            weight = 1.0 / (i + 1) 
            total_weight += weight
            
            w_threat += float(g['threat']) * weight
            w_creativity += float(g['creativity']) * weight
            w_mins += g['minutes'] * weight
            w_saves += g['saves'] * weight
            w_bonus += g['bonus'] * weight
            w_yel += g['yellow_cards'] * weight
            w_conceded += g['goals_conceded'] * weight
            w_cs += g['clean_sheets'] * weight

        avg_threat = w_threat / total_weight
        avg_creativity = w_creativity / total_weight
        avg_mins = w_mins / total_weight
        avg_saves = w_saves / total_weight
        avg_bonus = w_bonus / total_weight
        avg_yel = w_yel / total_weight
        avg_conceded = w_conceded / total_weight
        avg_cs = w_cs / total_weight
        
        if avg_mins < 30: # Filter bench players
            return None

        # Apply Global Conversion Rates
        pos = player['element_type']
        ratios = self.global_ratios.get(pos, {'threat_to_goal': 0, 'creativity_to_assist': 0})
        
        base_goals = avg_threat * ratios['threat_to_goal']
        base_assists = avg_creativity * ratios['creativity_to_assist']

        # Fixture Adjustment
        team_id = player['team_id']
        fixture = self.team_fixtures.get(team_id)
        if not fixture: return None
            
        diff = fixture['difficulty']
        is_home = fixture['is_home']
        
        # Difficulty Factors
        diff_factor = 1 + (3 - diff) * DIFFICULTY_STEP
        home_factor = HOME_ADVANTAGE if is_home else 1.0
        total_factor = diff_factor * home_factor
        
        concede_factor = 1 + (diff - 3) * DIFFICULTY_STEP

        proj_goals = base_goals * total_factor
        proj_assists = base_assists * total_factor
        proj_cs = avg_cs * total_factor 
        proj_gc = avg_conceded * concede_factor
        proj_saves = avg_saves * concede_factor
        proj_bonus = avg_bonus * total_factor
        
        return {
            'player_id': player_id,
            'name': player['web_name'],
            'pos': player['element_type'],
            'cost': player['now_cost'] / 10.0,
            'opponent_diff': diff,
            'is_home': is_home,
            'opponent_team_id': fixture['opponent'],
            'stats': {
                'minutes': avg_mins,
                'goals': proj_goals,
                'assists': proj_assists,
                'clean_sheets': proj_cs,
                'conceded': proj_gc,
                'saves': proj_saves,
                'bonus': proj_bonus,
                'yellow_cards': avg_yel
            }
        }


    def calculate_points(self, proj):
        stats = proj['stats']
        pos = proj['pos']
        
        pts = 0
        
        # Minutes
        if stats['minutes'] >= 60:
            pts += PTS_MINS_60
        elif stats['minutes'] > 0:
            pts += PTS_MINS_1
            
        # Goals
        pts += stats['goals'] * {1: PTS_GOAL_GK, 2: PTS_GOAL_DEF, 3: PTS_GOAL_MID, 4: PTS_GOAL_FWD}[pos]
        
        # Assists
        pts += stats['assists'] * PTS_ASSIST
        
        # Clean Sheets
        if pos in [1, 2]: # GK/DEF
            pts += stats['clean_sheets'] * PTS_CS_DEF
        elif pos == 3: # MID
            pts += stats['clean_sheets'] * PTS_CS_MID
            
        # Saves
        pts += (stats['saves'] / 3) * PTS_SAVES_3
        
        # Conceded
        if pos in [1, 2]:
            pts += (stats['conceded'] / 2) * PTS_GC_2_DEF
            
        # Cards
        pts += stats['yellow_cards'] * PTS_YEL
        
        # Bonus
        pts += stats['bonus']
        
        return pts

    def generate_predictions(self):
        gw = self.fetch_data()
        if not gw: 
            return []
        
        self.train_global_ratios()
        
        print(f"Calculating projections for Gameweek {gw}...")
        
        projections = []
        for pid in self.players:
            proj = self.calculate_expected_stats(pid)
            if proj:
                pts = self.calculate_points(proj)
                
                # Format for Supabase 'predictions' table
                # Columns: player_id, gw, predicted_pts, opponent_team_id, is_home, difficulty
                projections.append({
                    "player_id": proj['player_id'],
                    "gw": gw,
                    "predicted_pts": float(f"{pts:.2f}"),
                    "opponent_team_id": proj['opponent_team_id'],
                    "is_home": proj['is_home'],
                    "difficulty": proj['opponent_diff']
                })
                
        return projections

if __name__ == "__main__":
    pred = Predictor()
    preds = pred.generate_predictions()
    print(f"Generated {len(preds)} predictions.")
