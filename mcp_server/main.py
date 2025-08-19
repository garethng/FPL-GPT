import asyncio
from typing import List, Optional, AsyncIterator, Dict
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, inspect, Boolean
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
from mcp.types import Content

from fpl import FPL

load_dotenv()

# --- Database Setup (largely unchanged) ---
import os
# 优先使用环境变量中的数据库URL，如果没有设置则使用默认路径
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
default_db_path = os.path.join(parent_dir, 'db/fpl.db')
DATABASE_URL = os.environ.get('DATABASE_URL', f"sqlite:///{default_db_path}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- SQLAlchemy Models (unchanged) ---
class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    players = relationship("Player", back_populates="team")

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String)
    second_name = Column(String)
    web_name = Column(String)
    team_id = Column(Integer, ForeignKey("teams.id"))
    team_code = Column(Integer)
    element_type = Column(Integer)
    now_cost = Column(Integer)
    total_points = Column(Integer)
    minutes = Column(Integer)
    goals_scored = Column(Integer)
    assists = Column(Integer)
    clean_sheets = Column(Integer)
    goals_conceded = Column(Integer)
    own_goals = Column(Integer)
    penalties_saved = Column(Integer)
    penalties_missed = Column(Integer)
    yellow_cards = Column(Integer)
    red_cards = Column(Integer)
    saves = Column(Integer)
    bonus = Column(Integer)
    bps = Column(Integer)
    influence = Column(Float)
    creativity = Column(Float)
    threat = Column(Float)
    ict_index = Column(Float)
    event_points = Column(Integer)
    team = relationship("Team", back_populates="players")
    predictions = relationship("Prediction", back_populates="player")
    history = relationship("PlayerHistory", back_populates="player")

class PlayerHistory(Base):
    __tablename__ = "player_history"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    fixture_id = Column(Integer)
    opponent_team_id = Column(Integer, ForeignKey("teams.id"))
    total_points = Column(Integer)
    was_home = Column(Boolean)
    kickoff_time = Column(String)
    round = Column(Integer)
    minutes = Column(Integer)
    goals_scored = Column(Integer)
    assists = Column(Integer)
    clean_sheets = Column(Integer)
    goals_conceded = Column(Integer)
    own_goals = Column(Integer)
    penalties_saved = Column(Integer)
    penalties_missed = Column(Integer)
    yellow_cards = Column(Integer)
    red_cards = Column(Integer)
    saves = Column(Integer)
    bonus = Column(Integer)
    bps = Column(Integer)
    influence = Column(Float)
    creativity = Column(Float)
    threat = Column(Float)
    ict_index = Column(Float)
    player = relationship("Player", back_populates="history")
    opponent_team = relationship("Team")

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    gw = Column(Integer)
    predicted_pts = Column(Float)
    opponent_team_id = Column(Integer, ForeignKey("teams.id"))
    is_home = Column(Boolean)
    difficulty = Column(Integer)
    player = relationship("Player", back_populates="predictions")
    opponent_team = relationship("Team")


# --- Pydantic Models for tool outputs ---
class FPLBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class PredictionBase(FPLBaseModel):
    gw: int
    predicted_pts: float
    opponent_team: Optional[str] = None
    is_home: Optional[bool] = None
    difficulty: Optional[int] = None

class PlayerHistoryBase(FPLBaseModel):
    round: int
    opponent_team: str
    was_home: bool
    total_points: int
    minutes: int
    goals_scored: int
    assists: int
    clean_sheets: int
    bonus: int

class PlayerBase(FPLBaseModel):
    id: int
    first_name: str
    second_name: str
    web_name: str
    team_id: int
    now_cost: int
    total_points: int

class TeamBase(FPLBaseModel):
    id: int
    name: str

class MyTeamPlayer(BaseModel):
    player_id: int
    is_captain: bool
    is_vice_captain: bool
    position: int # This is the pick order, not pitch position
    player_position: str # GK, DEF, MID, FWD
    name: str
    cost: float

class Chip(BaseModel):
    name: str
    status: str

class MyTeam(BaseModel):
    players: List[MyTeamPlayer]
    gameweek: int
    chips: List[Chip]
    free_transfers: int
    used_budget: float
    total_points: int
    overall_rank: int

# --- Lifespan and Context Management ---
@dataclass
class AppContext:
    """Application context to hold the database session."""
    db: Session
    fpl_client: FPL

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage the database session and FPL client lifecycle."""
    db = SessionLocal()
    fpl_client = FPL()
    email = os.getenv("FPL_EMAIL")
    password = os.getenv("FPL_PASSWORD")
    if email and password:
        try:
            await fpl_client.login_v2(email, password)
        except Exception as e:
            # Handle login failure gracefully, e.g., log a warning
            print(f"FPL login failed: {e}")
            fpl_client = None # Or handle differently
    else:
        print("FPL_EMAIL and/or FPL_PASSWORD not set in .env file.")
        fpl_client = None

    try:
        yield AppContext(db=db, fpl_client=fpl_client)
    finally:
        db.close()


def get_player_position(element_type: int) -> str:
    """Converts player element type to a position string."""
    return {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD"
    }.get(element_type, "UNK")


# --- MCP Server and Tool Definitions ---
app = FastMCP(lifespan=app_lifespan)
T_AppContext = Context[ServerSession, AppContext]

@app.tool()
async def list_teams(ctx: T_AppContext) -> List[TeamBase]:
    """Lists all teams in the Fantasy Premier League."""
    db = ctx.request_context.lifespan_context.db
    teams = db.query(Team).all()
    return [TeamBase.model_validate(t) for t in teams]

@app.tool()
async def get_team(ctx: T_AppContext, team_id: int) -> TeamBase | Content:
    """Gets a specific team by its ID."""
    db = ctx.request_context.lifespan_context.db
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return Content(text=f"Team with id {team_id} not found")
    return TeamBase.model_validate(team)

@app.tool()
async def list_players(ctx: T_AppContext, name: Optional[str] = None) -> List[PlayerBase]:
    """
    Lists players. If a name is provided, it filters players whose web_name contains the name.
    """
    db = ctx.request_context.lifespan_context.db
    query = db.query(Player)
    if name:
        query = query.filter(Player.web_name.contains(name))
    players = query.all()
    return [PlayerBase.model_validate(p) for p in players]

@app.tool()
async def get_player(ctx: T_AppContext, player_id: int) -> PlayerBase | Content:
    """Gets a specific player by their ID."""
    db = ctx.request_context.lifespan_context.db
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        return Content(text=f"Player with id {player_id} not found")
    return PlayerBase.model_validate(player)

@app.tool()
async def get_player_predictions(ctx: T_AppContext, player_id: int) -> List[PredictionBase] | Content:
    """Gets future gameweek predictions for a specific player."""
    db = ctx.request_context.lifespan_context.db
    
    # 获取球员预测，并直接使用数据库中的对阵信息
    predictions = db.query(Prediction).filter(Prediction.player_id == player_id).order_by(Prediction.gw).all()
    if not predictions:
        return Content(text=f"Predictions not found for player with id {player_id}")
    
    # 构建Pydantic模型列表作为返回结果
    result = []
    for p in predictions:
        result.append(
            PredictionBase(
                gw=p.gw,
                predicted_pts=p.predicted_pts,
                opponent_team=p.opponent_team.name if p.opponent_team else "N/A",
                is_home=p.is_home,
                difficulty=p.difficulty
            )
        )
        
    return result


@app.tool()
async def get_player_history(ctx: T_AppContext, player_id: int) -> List[PlayerHistoryBase] | Content:
    """Gets the past gameweek performance history for a specific player."""
    db = ctx.request_context.lifespan_context.db
    
    history = db.query(PlayerHistory).filter(PlayerHistory.player_id == player_id).order_by(PlayerHistory.round).all()
    if not history:
        return Content(text=f"History not found for player with id {player_id}")
    
    result = []
    for h in history:
        result.append(
            PlayerHistoryBase(
                round=h.round,
                opponent_team=h.opponent_team.name if h.opponent_team else "N/A",
                was_home=h.was_home,
                total_points=h.total_points,
                minutes=h.minutes,
                goals_scored=h.goals_scored,
                assists=h.assists,
                clean_sheets=h.clean_sheets,
                bonus=h.bonus,
            )
        )
        
    return result


@app.tool()
async def get_my_team(ctx: T_AppContext) -> MyTeam | Content:
    """
    Retrieves the logged-in user's team for the current gameweek,
    including available chips, free transfers, total points and overall rank.
    """
    fpl_client = ctx.request_context.lifespan_context.fpl_client
    if not fpl_client:
        return Content(text="FPL client not authenticated. Check server logs.")

    try:
        user = await fpl_client.get_user()
        gameweek = user.current_event
        
        # Correctly fetch the team picks and transfer status
        picks = await user.get_team()
        transfers_status = await user.get_transfers_status()
        # Explicitly fetch chips status
        user_chips = await user.get_chips()

        team_players = []
        player_ids = [p['element'] for p in picks]
        
        # Fetch player details from the database
        db_players = ctx.request_context.lifespan_context.db.query(Player).filter(Player.id.in_(player_ids)).all()
        players_map = {p.id: p for p in db_players}

        total_cost = 0
        for pick in picks:
            player_id = pick['element']
            player_details = players_map.get(player_id)
            if player_details:
                player_cost = player_details.now_cost / 10.0
                total_cost += player_cost
                team_players.append(
                    MyTeamPlayer(
                        player_id=player_id,
                        is_captain=pick['is_captain'],
                        is_vice_captain=pick['is_vice_captain'],
                        position=pick['position'],
                        player_position=get_player_position(player_details.element_type),
                        name=player_details.web_name,
                        cost=player_cost
                    )
                )
        
        # Get available chips
        chips = []
        for chip in user_chips:
            chips.append(Chip(name=chip['name'], status=chip['status_for_entry']))

        # Get free transfers
        free_transfers = transfers_status.get('limit', 0)

        # Get total points and rank
        total_points = getattr(user, 'summary_overall_points', 0)
        overall_rank = getattr(user, 'summary_overall_rank', 0)

        return MyTeam(
            players=team_players, 
            gameweek=gameweek,
            chips=chips,
            free_transfers=free_transfers,
            used_budget=round(total_cost, 1),
            total_points=total_points,
            overall_rank=overall_rank
        )
    except Exception as e:
        return Content(text=f"An error occurred: {e}")

# To run this server for development, run the following command from the project root:
# mcp dev mcp_server/main.py
if __name__ == "__main__":
    app.run(transport="streamable-http")