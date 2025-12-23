#!/usr/bin/env python3
"""
FPL 价格变动监控 - 多数据源聚合
从三个数据源获取价格预测数据并发送到飞书
"""

import requests
import json
import os
import sys
from typing import Dict, List, Optional
from datetime import datetime
import unicodedata


class FPLPriceMonitor:
    """FPL 价格监控器"""
    
    # 三个数据源
    SOURCES = {
        'ffhub': 'https://allaboutfantasy.cn/api/getpricepredict?source=ffhub',
        'fix': 'https://allaboutfantasy.cn/api/getpricepredict?source=fix',
        'livefpl': 'https://allaboutfantasy.cn/api/getpricepredict?source=livefpl'
    }
    
    def __init__(self, feishu_webhook: Optional[str] = None, user_webhooks: Dict[int, str] = None):
        """
        初始化监控器
        
        Args:
            feishu_webhook: 默认飞书 webhook URL
            user_webhooks: 用户 ID 到 webhook URL 的映射字典 {team_id: webhook_url}
        """
        self.feishu_webhook = feishu_webhook or os.getenv('FEISHU_WEBHOOK')
        self.user_webhooks = user_webhooks or {}
        
        # 处理 team_id (保持向后兼容)
        tid = os.getenv('FPL_TEAM_ID')
        try:
            self.team_id = int(tid) if tid else None
        except (ValueError, TypeError):
            self.team_id = None
            
        self.monitored_player_ids = set()
        self.data_cache = {}
        
        # FPL 静态数据缓存
        self.player_id_map = {} # id -> web_name
        self.player_name_map = {} # web_name -> id
        self.init_fpl_data()

    def init_fpl_data(self):
        """初始化 FPL 静态数据（用于 ID 和 名字 的转换）"""
        try:
            print("🔄 正在获取 FPL 静态数据...")
            url = "https://fantasy.premierleague.com/api/bootstrap-static/"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            for player in data.get('elements', []):
                pid = player['id']
                web_name = player['web_name']
                # 同时也保存 full name 以防万一，但 web_name 通常是标准
                self.player_id_map[pid] = web_name
                self.player_name_map[web_name] = pid
                # 也可以映射 full name
                full_name = f"{player['first_name']} {player['second_name']}"
                self.player_name_map[full_name] = pid
                
            print(f"✅ FPL 静态数据获取成功 (共 {len(self.player_id_map)} 名球员)")
            
            # 获取当前 GW
            self.current_gw = 1
            for event in data.get('events', []):
                if event.get('is_current', False):
                    self.current_gw = event['id']
                    break
                # 如果没有 current，找 next 的前一个
                elif event.get('is_next', False):
                    self.current_gw = max(1, event['id'] - 1)
                    break
            print(f"📅 当前/最近 Gameweek: {self.current_gw}")
            
        except Exception as e:
            print(f"❌ FPL 静态数据获取失败: {e}")

    def get_user_squad_names(self, team_id: int) -> List[str]:
        """获取用户当前阵容的球员名字列表"""
        if not team_id:
            return []
            
        try:
            # 尝试获取 Picks (无需认证)
            # 注意：这获取的是该用户在该 GW 的阵容，不包含当周未生效的转会
            url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{self.current_gw}/picks/"
            response = requests.get(url, timeout=10)
            
            # 如果该 GW 还没开始或没数据，可能返回 404，尝试上一周
            if response.status_code == 404 and self.current_gw > 1:
                 url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{self.current_gw - 1}/picks/"
                 response = requests.get(url, timeout=10)
            
            response.raise_for_status()
            data = response.json()
            
            player_names = []
            for pick in data.get('picks', []):
                pid = pick['element']
                pname = self.player_id_map.get(pid)
                if pname:
                    player_names.append(pname)
            
            return player_names
        except Exception as e:
            print(f"❌ 获取用户 {team_id} 阵容失败: {e}")
            return []

    def fetch_data(self, source_name: str, url: str) -> Optional[Dict]:
        """
        从指定数据源获取数据
        
        Args:
            source_name: 数据源名称
            url: API URL
            
        Returns:
            数据字典或 None
        """
        try:
            print(f"🔍 正在获取 {source_name} 数据...")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            print(f"✅ {source_name} 数据获取成功")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ {source_name} 数据获取失败: {e}")
            return None
    
    def fetch_all_sources(self) -> Dict[str, Dict]:
        """
        从所有数据源获取数据
        
        Returns:
            所有数据源的数据字典
        """
        all_data = {}
        
        for source_name, url in self.SOURCES.items():
            data = self.fetch_data(source_name, url)
            if data:
                all_data[source_name] = data
        
        self.data_cache = all_data
        return all_data

    def is_tonight(self, change_time: str) -> bool:
        """
        判断 change_time 是否是今晚（tonight）。

        Args:
            change_time: 变动时间字符串

        Returns:
            是否为 tonight
        """
        if not change_time or change_time == 'Unknown':
            return False
        return 'tonight' in str(change_time).lower()

    def normalize_name(self, name: str) -> str:
        """用于合并去重的名字规范化：去重音、去空白、转小写。"""
        if not name:
            return ""
        s = str(name).strip()
        s = "".join(
            ch for ch in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(ch)
        )
        s = " ".join(s.split())
        return s.lower()

    def normalize_team(self, team: str) -> str:
        if not team:
            return ""
        return " ".join(str(team).strip().split()).lower()

    def normalize_position(self, position: str) -> str:
        """将不同来源的位置统一到 GK/DEF/MID/FOR。"""
        if not position:
            return "Unknown"
        p = str(position).strip().lower()
        mapping = {
            "goalkeeper": "GK",
            "gk": "GK",
            "defender": "DEF",
            "def": "DEF",
            "midfielder": "MID",
            "mid": "MID",
            "forward": "FOR",
            "for": "FOR",
            "fwd": "FOR",
            "striker": "FOR",
        }
        return mapping.get(p, str(position).strip().upper())

    def extract_player_id(self, player: Dict) -> Optional[str]:
        """尽量从数据源中提取稳定的球员 ID；提取不到则返回 None。"""
        candidates = [
            "PlayerID", "PlayerId", "player_id", "playerId",
            "id", "ID", "element", "Element", "code", "Code"
        ]
        for k in candidates:
            if k in player and player.get(k) not in (None, "", "Unknown"):
                return str(player.get(k))
        return None
    
    def analyze_source_data(self, source_name: str, data: Dict, 
                           rise_threshold: float = 80, 
                           fall_threshold: float = -80) -> Dict:
        """
        分析单个数据源的数据
        
        Args:
            source_name: 数据源名称
            data: 数据
            rise_threshold: 上涨阈值
            fall_threshold: 下跌阈值
            
        Returns:
            分析结果
        """
        # 判断数据格式
        if 'list' in data:
            players = data.get('list', [])
            updated_time = data.get('updated_time', 'Unknown')
            
            # 提取关键字段
            risers = []
            fallers = []
            
            for player in players:
                # 获取进度值，并确保是数值类型
                target_raw = player.get('Target',
                                        player.get('threshold',
                                                   player.get('progress', 0)))
                try:
                    target = float(target_raw) if target_raw else 0
                except (ValueError, TypeError):
                    target = 0
                
                # 获取额外字段
                change_time = player.get('ChangeTime', player.get('change', ''))

                # 根据数据源应用不同的筛选规则
                should_include = False

                if source_name in ['ffhub', 'fix']:
                    # ffhub 和 fix：仅保留今晚（tonight）会变价的数据
                    should_include = self.is_tonight(change_time)

                elif source_name == 'livefpl':
                    # livefpl：只要 progressTonight > 100 或 < -100
                    progress_tonight_raw = player.get('progressTonight', '')
                    try:
                        progress_tonight = float(progress_tonight_raw) if progress_tonight_raw else 0
                        if abs(progress_tonight) > 100:
                            should_include = True
                    except (ValueError, TypeError):
                        should_include = False
                
                # 如果符合条件，添加到对应列表
                if should_include:
                    raw_position = player.get('Position', player.get('position', 'Unknown'))
                    player_data = {
                        'merge_key': None,
                        'name': player.get('PlayerName', player.get('name', 'Unknown')),
                        'team': player.get('Team', player.get('team', 'Unknown')),
                        'position': self.normalize_position(raw_position),
                        'price': player.get('Value',
                                            player.get('value',
                                                       player.get('price', 0))),
                        'ownership': player.get('Ownership', player.get('ownership', 0))
                    }

                    # 注意：不同数据源的“ID”口径可能不同，会导致同一球员无法合并；
                    # 因此合并键统一使用（去重音后的）姓名 + 球队。
                    norm_name = self.normalize_name(player_data.get('name', ''))
                    norm_team = self.normalize_team(player_data.get('team', ''))
                    player_data['merge_key'] = f"name:{norm_name}|team:{norm_team}"
                    
                    if target >= 0:  # 上涨
                        risers.append(player_data)
                    else:  # 下跌
                        fallers.append(player_data)
        else:
            return {
                'source': source_name,
                'error': '未知数据格式'
            }
        
        # 排序
        self.sort_players(risers, 'risers')
        self.sort_players(fallers, 'fallers')
        
        return {
            'source': source_name,
            'updated_time': updated_time,
            'total_players': len(players),
            'risers': risers,  # 返回全部符合条件的
            'fallers': fallers,
            'risers_count': len(risers),
            'fallers_count': len(fallers)
        }

    def get_time_priority(self, change_time: str) -> int:
        if not change_time:
            return 2

        change_time_lower = change_time.lower()
        if 'tonight' in change_time_lower:
            return 0
        if 'tomorrow' in change_time_lower:
            return 1
        return 2

    def sort_players(self, players: List[Dict], player_type: str) -> None:
        # 由于合并消息已取消 progress/progress_tonight，这里按持有率（高->低）再按名字排序
        def ownership_value(player: Dict) -> float:
            raw = player.get('ownership', 0)
            try:
                return float(raw)
            except (ValueError, TypeError):
                return 0.0

        players.sort(key=lambda p: (-ownership_value(p), str(p.get('name', ''))))
    

    def merge_players_by_sources(self, analyses: List[Dict]) -> Dict[str, List[Dict]]:
        """
        将多个数据源的球员列表合并，按球员聚合来源。

        Returns:
            {'risers': [...], 'fallers': [...]}
        """
        merged = {'risers': {}, 'fallers': {}}

        for analysis in analyses:
            source = analysis.get('source', 'Unknown')
            if 'error' in analysis:
                continue

            for player_type in ('risers', 'fallers'):
                for p in analysis.get(player_type, []):
                    key = p.get('merge_key')
                    if not key:
                        # 兜底：用规范化名字+球队合并，避免 position/拼写不一致导致拆分
                        norm_name = self.normalize_name(p.get('name', ''))
                        norm_team = self.normalize_team(p.get('team', ''))
                        key = f"name:{norm_name}|team:{norm_team}"
                    if key not in merged[player_type]:
                        merged[player_type][key] = {
                            'name': p.get('name', 'Unknown'),
                            'team': p.get('team', 'Unknown'),
                            'position': self.normalize_position(p.get('position', 'Unknown')),
                            'price': p.get('price', 0),
                            'ownership': p.get('ownership', 0),
                            'sources': set()
                        }
                    else:
                        # 合并时做一点“择优”：持有率更高的覆盖（不同源小数位差异时更稳定）
                        try:
                            cur_own = float(merged[player_type][key].get('ownership', 0))
                        except (ValueError, TypeError):
                            cur_own = 0.0
                        try:
                            new_own = float(p.get('ownership', 0))
                        except (ValueError, TypeError):
                            new_own = 0.0
                        if new_own > cur_own:
                            merged[player_type][key]['ownership'] = p.get('ownership', merged[player_type][key].get('ownership', 0))

                        # position 统一后保持成 GK/DEF/MID/FOR
                        merged[player_type][key]['position'] = self.normalize_position(
                            merged[player_type][key].get('position', p.get('position', 'Unknown'))
                        )
                    merged[player_type][key]['sources'].add(source)

        risers = list(merged['risers'].values())
        fallers = list(merged['fallers'].values())
        self.sort_players(risers, 'risers')
        self.sort_players(fallers, 'fallers')

        # 将 sources set 转成排序后的 list，方便格式化
        for p in risers + fallers:
            p['sources'] = sorted(list(p.get('sources', [])))

        return {'risers': risers, 'fallers': fallers}

    def format_merged_players_as_string(self, players: List[Dict], player_type: str) -> str:
        """
        按参考格式输出（编号 + emoji + 两段式详情），并在位置之后追加数据源。
        """
        is_risers = player_type == "risers"
        header_emoji = "📈" if is_risers else "📉"
        header_text = "即将上涨" if is_risers else "即将下跌"
        item_emoji = "🔺" if is_risers else "🟢"

        if not players:
            return f"{header_emoji} {header_text} (共 0 人)\n暂无符合条件的球员"

        # 不要输出任何空白行：每个球员严格两行（信息行 + 价格行）
        lines = [f"{header_emoji} {header_text} (共 {len(players)} 人)"]

        for i, player in enumerate(players, 1):
            sources = ",".join(player.get('sources', [])) or "Unknown"
            name = player.get('name', 'Unknown')
            team = player.get('team', 'Unknown')
            position = self.normalize_position(player.get('position', 'Unknown'))
            price = player.get('price', 0)
            ownership = player.get('ownership', 0)

            lines.append(f"{i}. {item_emoji} {name} ({team}) - {position} ({sources})")
            lines.append(f"   价格: £{price}m | 持有率: {ownership}%")

        return "\n".join(lines).rstrip()

    def build_feishu_message_merged(self, analyses: List[Dict]) -> Dict:
        """
        构建飞书消息（合并三个数据源，且仅展示 tonight）。
        """
        merged = self.merge_players_by_sources(analyses)

        risers_text = self.format_merged_players_as_string(merged.get('risers', []), "risers")
        fallers_text = self.format_merged_players_as_string(merged.get('fallers', []), "fallers")

        # 分组之间也不输出空白行
        text = f"{risers_text}\n{fallers_text}"

        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": "🏆 FPL 价格变动监控（合并）",
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": text
                                }
                            ]
                        ]
                    }
                }
            }
        }
    
    def filter_analysis_for_user(self, analysis: Dict, user_squad_names: List[str]) -> Dict:
        """为特定用户筛选分析结果（基于名字匹配）"""
        if 'error' in analysis:
            return analysis
            
        filtered_analysis = analysis.copy()
        
        # 筛选 Risers
        filtered_risers = []
        for player in analysis.get('risers', []):
            # 模糊匹配：检查预测的名字是否包含在用户阵容名字中，或者用户阵容名字包含预测名字
            # 这里简单起见，使用包含关系，因为 web_name 有时会有差异
            p_name = player['name']
            
            # 尝试直接匹配
            if p_name in user_squad_names:
                filtered_risers.append(player)
                continue
                
            # 尝试部分匹配 (例如 Son Heung-min vs Son)
            for user_p_name in user_squad_names:
                if p_name in user_p_name or user_p_name in p_name:
                    filtered_risers.append(player)
                    break
        
        filtered_analysis['risers'] = filtered_risers
        filtered_analysis['risers_count'] = len(filtered_risers)
        
        # 筛选 Fallers
        filtered_fallers = []
        for player in analysis.get('fallers', []):
            p_name = player['name']
            if p_name in user_squad_names:
                filtered_fallers.append(player)
                continue
            for user_p_name in user_squad_names:
                if p_name in user_p_name or user_p_name in p_name:
                    filtered_fallers.append(player)
                    break
                    
        filtered_analysis['fallers'] = filtered_fallers
        filtered_analysis['fallers_count'] = len(filtered_fallers)
        
        return filtered_analysis

    def build_combined_feishu_message(self, analyses: List[Dict], title: str = "🏆 FPL 价格变动监控") -> Dict:
        """
        构建合并的飞书消息（多个数据源聚合）
        
        Args:
            analyses: 分析结果列表
            title: 消息标题
            
        Returns:
            飞书消息体
        """
        if not analyses:
            return {}
            
        # 1. 聚合数据
        merged_risers = {}
        merged_fallers = {}
        
        def normalize_position(pos):
            """标准化位置名称"""
            if not pos: return ""
            pos = pos.upper()
            if 'MID' in pos: return 'MID'
            if 'FOR' in pos or 'FWD' in pos: return 'FOR'
            if 'DEF' in pos: return 'DEF'
            if 'GOA' in pos or 'GKP' in pos: return 'GKP'
            return pos

        def process_players(player_list, target_dict, source_name):
            for p in player_list:
                name = p.get('name')
                team = p.get('team')
                # 唯一键：名字 + 球队 (防止同名)
                key = (name, team)
                
                if key not in target_dict:
                    target_dict[key] = {
                        'name': name,
                        'team': team,
                        'position': normalize_position(p.get('position', '')),
                        'price': p.get('price'),
                        'ownership': p.get('ownership', 0),
                        'sources': set()
                    }
                
                # 记录数据源
                target_dict[key]['sources'].add(source_name)
                # 更新持有率（取最大值）
                current_own = target_dict[key]['ownership']
                new_own = p.get('ownership', 0)
                try:
                    if float(new_own) > float(current_own):
                        target_dict[key]['ownership'] = new_own
                except (ValueError, TypeError):
                    pass

        for analysis in analyses:
            source = analysis.get('source', 'Unknown')
            if 'error' in analysis:
                continue
                
            process_players(analysis.get('risers', []), merged_risers, source)
            process_players(analysis.get('fallers', []), merged_fallers, source)
            
        # 2. 排序 (按持有率降序)
        def get_ownership(item):
            try:
                return float(item['ownership'])
            except (ValueError, TypeError):
                return 0

        sorted_risers = sorted(merged_risers.values(), key=get_ownership, reverse=True)
        sorted_fallers = sorted(merged_fallers.values(), key=get_ownership, reverse=True)
        
        # 3. 构建文本
        full_text = ""
        
        # Risers
        if sorted_risers:
            full_text += f"📈 即将上涨 (共 {len(sorted_risers)} 人)\n"
            for i, p in enumerate(sorted_risers, 1):
                sources_str = ",".join(sorted(p['sources']))
                full_text += f"{i}. 🔺 {p['name']} ({p['team']}) - {p['position']} ({sources_str})\n"
                full_text += f"   价格: £{p['price']}m | 持有率: {p['ownership']}%\n"
        
        # Fallers
        if sorted_fallers:
            if full_text: full_text += "\n"
            full_text += f"📉 即将下跌 (共 {len(sorted_fallers)} 人)\n"
            for i, p in enumerate(sorted_fallers, 1):
                sources_str = ",".join(sorted(p['sources']))
                full_text += f"{i}. 🟢 {p['name']} ({p['team']}) - {p['position']} ({sources_str})\n"
                full_text += f"   价格: £{p['price']}m | 持有率: {p['ownership']}%\n"
                
        if not full_text:
            full_text = "暂无相关变动"
            
        full_text = full_text.strip()

        # 构建消息
        message = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": full_text
                                }
                            ]
                        ]
                    }
                }
            }
        }
        
        return message

    def send_to_webhook(self, message: Dict, webhook_url: str) -> bool:
        """发送消息到指定 Webhook"""
        if not webhook_url:
            return False
        
        try:
            # print(f"📤 正在发送消息到 {webhook_url[:10]}...")
            response = requests.post(
                webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                return True
            return False
        except Exception as e:
            print(f"❌ 发送消息失败: {e}")
            return False

    def run(self, rise_threshold: float = 80, fall_threshold: float = -80):
        """
        执行完整的监控流程
        """
        print("="*80)
        print("🏆 FPL 价格变动监控启动")
        print("="*80)
        
        # 1. 获取所有数据源的数据
        all_data = self.fetch_all_sources()
        
        if not all_data:
            print("❌ 未能获取任何数据源的数据")
            sys.exit(1)
        
        print(f"\n✅ 成功获取 {len(all_data)} 个数据源的数据\n")
        
        # 2. 分析每个数据源 (全局)
        analyses = []
        for source_name, data in all_data.items():
            print(f"📊 分析 {source_name} 数据...")
            analysis = self.analyze_source_data(
                source_name, data, rise_threshold, fall_threshold
            )
            analyses.append(analysis)
            
            print(f"   - 接近上涨: {analysis.get('risers_count', 0)} 人")
            print(f"   - 接近下跌: {analysis.get('fallers_count', 0)} 人")
        
        print()
        
        # 3. 发送全局通知 (Default Webhook)
        if self.feishu_webhook:
            print("="*80)
            print("📤 发送全局通知 (合并)")
            print("="*80)
            
            # 过滤掉没有结果的数据源用于聚合，但实际上 build_combined 已经能处理
            valid_global_analyses = [a for a in analyses if a.get('risers_count', 0) > 0 or a.get('fallers_count', 0) > 0]
            
            if valid_global_analyses:
                global_message = self.build_combined_feishu_message(valid_global_analyses, title="🏆 FPL 价格变动监控（合并）")
                print("--- Global Combined Message Content ---")
                print(json.dumps(global_message, indent=2, ensure_ascii=False))
                self.send_to_webhook(global_message, self.feishu_webhook)
            else:
                print("ℹ️ 无符合条件的变动，跳过全局通知")
        
        # 4. 发送个人通知 (User Webhooks)
        if self.user_webhooks:
            print("\n" + "="*80)
            print("👤 处理个人用户通知")
            print("="*80)
            
            for team_id, webhook_url in self.user_webhooks.items():
                print(f"🔍 检查用户 {team_id} 的阵容...")
                squad_names = self.get_user_squad_names(team_id)
                if not squad_names:
                    print(f"   ⚠️ 无法获取用户 {team_id} 的阵容或阵容为空")
                    continue
                    
                print(f"   ✅ 用户 {team_id} 阵容包含 {len(squad_names)} 名球员")
                
                # 收集该用户所有数据源的分析结果
                user_valid_analyses = []
                for analysis in analyses:
                    # 为用户筛选结果
                    user_analysis = self.filter_analysis_for_user(analysis, squad_names)
                    
                    if user_analysis.get('risers_count', 0) > 0 or user_analysis.get('fallers_count', 0) > 0:
                        print(f"   Found match in {analysis['source']}: +{user_analysis['risers_count']} / -{user_analysis['fallers_count']}")
                        user_valid_analyses.append(user_analysis)
                
                if user_valid_analyses:
                    print(f"   📤 正在合并 {len(user_valid_analyses)} 个数据源的通知发送给用户 {team_id}...")
                    combined_message = self.build_combined_feishu_message(user_valid_analyses, title="🏆 FPL 价格变动监控 (你的阵容)")
                    print(f"--- Combined User Message Content (User {team_id}) ---")
                    print(json.dumps(combined_message, indent=2, ensure_ascii=False))
                    if self.send_to_webhook(combined_message, webhook_url):
                        print(f"   ✅ 用户 {team_id} 通知发送成功")
                    else:
                        print(f"   ❌ 用户 {team_id} 通知发送失败")
                else:
                    print(f"   ℹ️ 用户 {team_id} 无相关价格变动")

        print("\n" + "="*80)
        print("✅ 监控任务完成")
        print("="*80)



def main():
    """主函数"""
    # 从环境变量读取飞书 webhook
    feishu_webhook = "https://www.feishu.cn/flow/api/trigger-webhook/2791fe5ac1644dfc97bb872bc41dce35"
    
    # 用户映射配置 (User ID -> Webhook URL)
    # 可以在这里添加具体的映射，或者从配置文件/环境变量读取
    # 示例:
    # user_webhooks = {
    #     123456: "https://www.feishu.cn/flow/api/trigger-webhook/...",
    #     789012: "https://www.feishu.cn/flow/api/trigger-webhook/..."
    # }
    user_webhooks = {
        "123097": "https://www.feishu.cn/flow/api/trigger-webhook/816cf2a06513b904a8830e68c13393b2",
        "2374827": "https://www.feishu.cn/flow/api/trigger-webhook/d22c4ccd36f78c3ca994631a959d5e47"
    }
    
    monitor = FPLPriceMonitor(feishu_webhook, user_webhooks=user_webhooks)
    
    # 运行监控
    monitor.run(rise_threshold=80, fall_threshold=-80)


if __name__ == "__main__":
    main()

