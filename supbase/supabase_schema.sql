-- Supabase Schema Migration for FPL-GPT
-- This schema matches the existing SQLite structure

-- Teams table
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT
);

-- Players table
CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    web_name TEXT,
    first_name TEXT,
    second_name TEXT,
    team_id INTEGER REFERENCES teams(id),
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
    chance_of_playing_next_round INTEGER,
    chance_of_playing_this_round INTEGER,
    status TEXT,
    news TEXT
);

-- Player history table
CREATE TABLE IF NOT EXISTS player_history (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    fixture_id INTEGER NOT NULL,
    opponent_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
    total_points INTEGER DEFAULT 0,
    was_home BOOLEAN DEFAULT false,
    kickoff_time TIMESTAMP,
    round INTEGER,
    minutes INTEGER DEFAULT 0,
    goals_scored INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    clean_sheets INTEGER DEFAULT 0,
    goals_conceded INTEGER DEFAULT 0,
    own_goals INTEGER DEFAULT 0,
    penalties_saved INTEGER DEFAULT 0,
    penalties_missed INTEGER DEFAULT 0,
    yellow_cards INTEGER DEFAULT 0,
    red_cards INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    bonus INTEGER DEFAULT 0,
    bps INTEGER DEFAULT 0,
    influence REAL DEFAULT 0,
    creativity REAL DEFAULT 0,
    threat REAL DEFAULT 0,
    ict_index REAL DEFAULT 0,
    
    -- 添加联合唯一约束
    CONSTRAINT player_history_player_fixture_unique UNIQUE (player_id, fixture_id)
);

-- Predictions table
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    player_id INTEGER REFERENCES players(player_id),
    gw INTEGER,
    predicted_pts REAL,
    opponent_team_id INTEGER REFERENCES teams(team_id),
    is_home BOOLEAN,
    difficulty INTEGER,
    CONSTRAINT predictions_player_fixture_unique UNIQUE (player_id, gw)
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_players_team_id ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_players_element_type ON players(element_type);
CREATE INDEX IF NOT EXISTS idx_players_now_cost ON players(now_cost);
CREATE INDEX IF NOT EXISTS idx_player_history_player_id ON player_history(player_id);
CREATE INDEX IF NOT EXISTS idx_player_history_round ON player_history(round);
CREATE INDEX IF NOT EXISTS idx_predictions_player_id ON predictions(player_id);
CREATE INDEX IF NOT EXISTS idx_predictions_gw ON predictions(gw);