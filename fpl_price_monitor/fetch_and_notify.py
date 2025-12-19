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
    
    def __init__(self, feishu_webhook: Optional[str] = None):
        """
        初始化监控器
        
        Args:
            feishu_webhook: 飞书 webhook URL
        """
        self.feishu_webhook = feishu_webhook or os.getenv('FEISHU_WEBHOOK')
        self.data_cache = {}
    
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
    
    def send_to_feishu(self, message: Dict) -> bool:
        """
        发送消息到飞书
        
        Args:
            message: 消息体
            
        Returns:
            是否发送成功
        """
        if not self.feishu_webhook:
            print("⚠️  未配置飞书 webhook，跳过发送")
            return False
        
        try:
            print(f"📤 正在发送消息到飞书...")
            response = requests.post(
                self.feishu_webhook,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print("✅ 消息发送成功")
                return True
            else:
                print(f"❌ 消息发送失败: {result}")
                return False
        except Exception as e:
            print(f"❌ 发送消息时出错: {e}")
            return False
    
    def run(self, rise_threshold: float = 80, fall_threshold: float = -80):
        """
        执行完整的监控流程
        
        Args:
            rise_threshold: 上涨阈值
            fall_threshold: 下跌阈值
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
        
        # 2. 分析每个数据源
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


        
        # 4. 合并三个数据源的结果后发送到飞书（只发送一次）
        if self.feishu_webhook:
            print("="*80)
            print("📤 开始发送消息到飞书")
            print("="*80)
            
            message = self.build_feishu_message_merged(analyses)
            if self.send_to_feishu(message):
                print("✅ 已发送合并消息")
            else:
                print("❌ 合并消息发送失败")
            print(message)
        
        print("\n" + "="*80)
        print("✅ 监控任务完成")
        print("="*80)


def main():
    """主函数"""
    # 从环境变量读取飞书 webhook
    feishu_webhook = "https://www.feishu.cn/flow/api/trigger-webhook/2791fe5ac1644dfc97bb872bc41dce35"
    
    if not feishu_webhook:
        print("⚠️  警告: 未设置 FEISHU_WEBHOOK 环境变量，将不会发送飞书通知")
    
    monitor = FPLPriceMonitor(feishu_webhook)
    
    # 运行监控
    monitor.run(rise_threshold=80, fall_threshold=-80)


if __name__ == "__main__":
    main()

