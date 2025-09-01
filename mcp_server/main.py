import asyncio
from typing import List, Optional, AsyncIterator, Dict, Union
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv
from supabase import create_client

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
from mcp.types import Content

from fpl import FPL
from logpilot.log import Log

logger = Log.get_logger("fpl-mcp-server")
load_dotenv()

# --- Database Setup ---
import os

# Get Supabase credentials from environment
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# Check if Supabase credentials are available
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase credentials (SUPABASE_URL and SUPABASE_KEY) are required and not found in environment variables")

# Initialize Supabase client
supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
logger.info("Supabase client initialized successfully")



# --- Pydantic Models for tool outputs ---
class FPLBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class PredictionBase(FPLBaseModel):
    player_id: int
    gw: int
    predicted_pts: float
    opponent_team: Optional[str] = None
    is_home: Optional[bool] = None
    difficulty: Optional[int] = None


class PlayerHistoryBase(FPLBaseModel):
    player_id: int
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
    player_id: int
    first_name: str
    second_name: str
    web_name: str
    team_id: int
    now_cost: int
    total_points: int
    element_type: int
    chance_of_playing_next_round: Optional[int] = None
    chance_of_playing_this_round: Optional[int] = None
    status: Optional[str] = None
    news: Optional[str] = None
    
    @property
    def cost(self) -> float:
        """返回球员身价（以百万为单位）"""
        return self.now_cost / 10.0
    
    @property
    def position(self) -> str:
        """返回球员位置（GK, DEF, MID, FWD）"""
        return get_player_position(self.element_type)
        
    @property
    def available(self) -> bool:
        """返回球员是否可用"""
        # 如果chance_of_playing_next_round为None或100，则认为球员可用
        if self.chance_of_playing_next_round is None:
            return True
        return self.chance_of_playing_next_round == 100

class TeamBase(FPLBaseModel):
    team_id: int
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
    
class FixtureInfo(FPLBaseModel):
    gameweek: int
    team_name: str
    opponent_name: str
    is_home: bool
    difficulty: int

# --- Lifespan and Context Management ---
@dataclass
class AppContext:
    """Application context to hold the Supabase client and FPL client."""
    fpl_client: FPL
    supabase: any

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage the Supabase client and FPL client lifecycle."""
    
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
        yield AppContext(fpl_client=fpl_client, supabase=supabase_client)
    finally:
        # No database connection to close
        pass


def get_player_position(element_type: int) -> str:
    """Converts player element type to a position string."""
    return {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD"
    }.get(element_type, "UNK")


# --- MCP Server and Tool Definitions ---
app = FastMCP(lifespan=app_lifespan, host="0.0.0.0", port=8000)
T_AppContext = Context[ServerSession, AppContext]

@app.tool()
async def list_teams(ctx: T_AppContext) -> List[TeamBase]:
    """Lists all teams in the Fantasy Premier League."""
    supabase = ctx.request_context.lifespan_context.supabase
    
    result = supabase.table("teams").select("*").execute()
    return [TeamBase.model_validate(t) for t in result.data]

@app.tool()
async def get_team(ctx: T_AppContext, team_id: int) -> Union[TeamBase, Content]:
    """Gets a specific team by its ID."""
    supabase = ctx.request_context.lifespan_context.supabase
    
    result = supabase.table("teams").select("*").eq("team_id", team_id).execute()
    if not result.data:
        return Content(text=f"Team with id {team_id} not found")
    return TeamBase.model_validate(result.data[0])

@app.tool()
async def list_players(ctx: T_AppContext, name: Optional[str] = None, min_cost: Optional[float] = None, max_cost: Optional[float] = None, available: Optional[bool] = None) -> List[PlayerBase]:
    """
    Lists players. If a name is provided, it filters players whose web_name contains the name.
    If min_cost or max_cost is provided, it filters players by their cost (in millions).
    If available is provided, it filters players by their availability status.
    """
    supabase = ctx.request_context.lifespan_context.supabase
    
    query = supabase.table("players").select("*")
    
    # 按名称筛选
    if name:
        # add or for name
        query = query.or_(f"web_name.ilike.{name},first_name.ilike.{name},second_name.ilike.{name}")
    
    # 按身价范围筛选
    if min_cost is not None:
        min_cost_db = int(min_cost * 10)  # 转换为数据库存储单位
        query = query.gte("now_cost", min_cost_db)
    
    if max_cost is not None:
        max_cost_db = int(max_cost * 10)  # 转换为数据库存储单位
        query = query.lte("now_cost", max_cost_db)
    
    # 按可用状态筛选
    if available is not None:
        if available:
            # 如果筛选可用球员，则选择chance_of_playing_next_round为100或为NULL的球员
            query = query.or_(f"chance_of_playing_next_round.eq.100,chance_of_playing_next_round.is.null")
        else:
            # 如果筛选不可用球员，则选择chance_of_playing_next_round不为100且不为NULL的球员
            query = query.neq("chance_of_playing_next_round", 100).not_.is_("chance_of_playing_next_round", "null")
    
    result = query.execute()
    return [PlayerBase.model_validate(p) for p in result.data]

@app.tool()
async def get_player(ctx: T_AppContext, player_id: int) -> Union[PlayerBase, Content]:
    """Gets a specific player by their ID."""
    supabase = ctx.request_context.lifespan_context.supabase
    
    result = supabase.table("players").select("*").eq("player_id", player_id).execute()
    if not result.data:
        return Content(text=f"Player with id {player_id} not found")
    return PlayerBase.model_validate(result.data[0])

@app.tool()
async def get_player_predictions(ctx: T_AppContext, player_id: int, gameweek: Optional[int] = None) -> Union[List[PredictionBase], Content]:
    """Gets future gameweek predictions for a specific player. If gameweek is provided, returns only that gameweek's prediction."""
    supabase = ctx.request_context.lifespan_context.supabase
    
    query = supabase.table("predictions").select("*, opponent_team:teams(name)").eq("player_id", player_id)
    
    # 如果指定了轮次，则只查询该轮次的预测
    if gameweek is not None:
        query = query.eq("gw", gameweek)
    
    # 按轮次排序
    result = query.order("gw").execute()
    
    if not result.data:
        if gameweek is not None:
            return Content(text=f"Prediction not found for player {player_id} in gameweek {gameweek}")
        else:
            return Content(text=f"Predictions not found for player with id {player_id}")
    
    # 构建Pydantic模型列表作为返回结果
    predictions = []
    for p in result.data:
        opponent_team_name = p.get('opponent_team', {}).get('name', 'N/A') if isinstance(p.get('opponent_team'), dict) else 'N/A'
        predictions.append(
            PredictionBase(
                player_id=player_id,
                gw=p['gw'],
                predicted_pts=p['predicted_pts'],
                opponent_team=opponent_team_name,
                is_home=p['is_home'],
                difficulty=p['difficulty']
            )
        )
    
    return predictions




@app.tool()
async def get_player_history(ctx: T_AppContext, player_id: int, gameweek: Optional[int] = None) -> Union[List[PlayerHistoryBase], Content]:
    """Gets the past gameweek performance history for a specific player."""
    supabase = ctx.request_context.lifespan_context.supabase
    
    query = supabase.table("player_history").select("*, opponent_team:teams(name)").eq("player_id", player_id).order("round")
    if gameweek is not None:
        query = query.eq("round", gameweek)
    result = query.execute()
    
    if not result.data:
        return Content(text=f"History not found for player with id {player_id}")
    
    history = []
    for h in result.data:
        opponent_team_name = h.get('opponent_team', {}).get('name', 'N/A') if isinstance(h.get('opponent_team'), dict) else 'N/A'
        history.append(
            PlayerHistoryBase(
                player_id=player_id,
                round=h['round'],
                opponent_team=opponent_team_name,
                was_home=h['was_home'],
                total_points=h['total_points'],
                minutes=h['minutes'],
                goals_scored=h['goals_scored'],
                assists=h['assists'],
                clean_sheets=h['clean_sheets'],
                bonus=h['bonus'],
            )
        )
    
    return history


@app.tool()
async def get_fixtures(ctx: T_AppContext, team_id: Optional[int] = None, gameweek: Optional[int] = 1) -> Union[List[FixtureInfo], Content]:
    """
    获取未来的对阵信息和对阵难度。
    
    参数:
    - team_id: 可选，特定球队的ID。如果提供，则只返回该球队的对阵。
    - gameweeks: 可选，要查询的特定轮次，默认为第一轮。
    
    返回:
    - 未来对阵信息列表，包括轮次、主队、客队、是否主场和难度系数。
    """
    supabase = ctx.request_context.lifespan_context.supabase
    
    try:
        # 从预测表中获取当前轮次

        
        # 获取所有球队
        teams_result = supabase.table("teams").select("team_id, name").execute()
        team_map = {team['team_id']: team['name'] for team in teams_result.data}
        
        # 构建基础查询 - 获取每个轮次中每个球队的一个预测记录
        query = supabase.table("predictions").select("gw, player:players(team_id), opponent_team_id, is_home, difficulty").gte("gw", gameweek)
        
        # 如果指定了球队ID，则只查询该球队的对阵
        if team_id:
            query = query.eq("player.team_id", team_id)
        
        result = query.execute()
        print(result.data)
        # 使用字典来确保每个球队和轮次组合只有一条记录
        fixtures_dict = {}
        for item in result.data:
            player_data = item.get('player', {})
            if isinstance(player_data, list) and player_data:
                player_data = player_data[0]
            
            team_id_val = player_data.get('team_id') if isinstance(player_data, dict) else None
            if not team_id_val:
                continue
            
            key = (team_id_val, item['gw'])
            if key not in fixtures_dict:
                fixtures_dict[key] = {
                    'gw': item['gw'],
                    'team_id': team_id_val,
                    'opponent_team_id': item['opponent_team_id'],
                    'is_home': item['is_home'],
                    'difficulty': item['difficulty']
                }
        
        # 构建结果
        fixtures_list = []
        for fixture_data in fixtures_dict.values():
            team_name = team_map.get(fixture_data['team_id'], "未知")
            opponent_name = team_map.get(fixture_data['opponent_team_id'], "未知")
            
            fixtures_list.append(
                FixtureInfo(
                    gameweek=fixture_data['gw'],
                    team_name=team_name,
                    opponent_name=opponent_name,
                    is_home=fixture_data['is_home'],
                    difficulty=fixture_data['difficulty']
                )
            )
        
        # 按轮次和球队名称排序
        fixtures_list.sort(key=lambda x: (x.gameweek, x.team_name))
        
        return fixtures_list
    except Exception as e:
        return Content(text=f"获取对阵信息时发生错误: {e}")


@app.tool()
async def get_my_team(ctx: T_AppContext) -> MyTeam | Content:
    """
    Retrieves the logged-in user's team for the current gameweek,
    including available chips, free transfers, total points and overall rank.
    """
    fpl_client = ctx.request_context.lifespan_context.fpl_client
    if not fpl_client:
        logger.error("FPL client not authenticated. Check server logs.")
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
        
        # Fetch player details from Supabase
        supabase = ctx.request_context.lifespan_context.supabase
        result = supabase.table("players").select("*").in_("player_id", player_ids).execute()
        players_map = {p['player_id']: p for p in result.data}

        total_cost = 0
        for pick in picks:
            player_id = pick['element']
            player_details = players_map.get(player_id)
            if player_details:
                player_cost = player_details['now_cost'] / 10.0
                total_cost += player_cost
                team_players.append(
                    MyTeamPlayer(
                        player_id=player_id,
                        is_captain=pick['is_captain'],
                        is_vice_captain=pick['is_vice_captain'],
                        position=pick['position'],
                        player_position=get_player_position(player_details['element_type']),
                        name=player_details['web_name'],
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
        logger.error(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        return Content(text=f"An error occurred: {e}")

@app.tool()
async def get_team_fixtures(ctx: T_AppContext, team_name: str, gameweek: Optional[int] = 1) -> Union[List[FixtureInfo], Content]:
    """
    根据球队名称获取特定球队的未来对阵信息。
    
    参数:
    - team_name: 球队名称（可以是部分名称，将进行模糊匹配）
    - gameweeks: 可选，要查询的特定轮次，默认为第一轮。
    
    返回:
    - 该球队的未来对阵信息列表，包括轮次、对手、是否主场和难度系数。
    """
    supabase = ctx.request_context.lifespan_context.supabase
    
    try:
        # 查找匹配的球队
        result = supabase.table("teams").select("*").ilike("name", f"%{team_name}%").execute()
        if not result.data:
            return Content(text=f"未找到名称包含 '{team_name}' 的球队。")
        team = result.data[0]
        team_id = team['team_id']
        
        # 使用get_fixtures函数获取该球队的对阵信息
        fixtures = await get_fixtures(ctx, team_id, gameweek)
        if isinstance(fixtures, Content):
            return fixtures
            
        # 只返回该球队的对阵信息

        return fixtures
    except Exception as e:
        return Content(text=f"获取球队对阵信息时发生错误: {e}")


# To run this server for development, run the following command from the project root:
# mcp dev mcp_server/main.py
if __name__ == "__main__":
    app.run(transport="streamable-http")