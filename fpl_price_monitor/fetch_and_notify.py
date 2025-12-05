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
    
    def is_within_two_days(self, change_time: str) -> bool:
        """
        判断 change_time 是否在两天内
        
        Args:
            change_time: 变动时间字符串
            
        Returns:
            是否在两天内
        """
        if not change_time or change_time == 'Unknown':
            return False
        
        change_time_lower = change_time.lower()
        
        # 匹配两天内的时间
        two_day_keywords = ['tonight', 'tomorrow']
        
        for keyword in two_day_keywords:
            if keyword in change_time_lower:
                return True
        
        return False
    
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
                progress_tonight_raw = player.get('progressTonight', '')
                progress_tonight_value = None
                if progress_tonight_raw:
                    try:
                        progress_tonight_value = float(progress_tonight_raw)
                    except (ValueError, TypeError):
                        progress_tonight_value = None
                
                # 根据数据源应用不同的筛选规则
                should_include = False
                
                if source_name in ['ffhub', 'fix']:
                    # ffhub 和 fix: 只要两天内的数据
                    if change_time and self.is_within_two_days(change_time):
                        should_include = True
                
                elif source_name == 'livefpl':
                    # livefpl: 只要 progressTonight > 100 或 < -100
                    try:
                        progress_tonight = float(progress_tonight_raw) if progress_tonight_raw else 0
                        if abs(progress_tonight) > 100:
                            should_include = True
                    except (ValueError, TypeError):
                        pass
                
                # 如果符合条件，添加到对应列表
                if should_include:
                    player_data = {
                        'name': player.get('PlayerName', player.get('name', 'Unknown')),
                        'team': player.get('Team', player.get('team', 'Unknown')),
                        'position': player.get('Position', player.get('position', 'Unknown')),
                        'price': player.get('Value',
                                            player.get('value',
                                                       player.get('price', 0))),
                        'ownership': player.get('Ownership', player.get('ownership', 0)),
                        'progress': target,
                        'change_time': change_time,
                        'progress_tonight': progress_tonight_value
                    }
                    
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
        def percent_value(player: Dict) -> float:
            if player.get('progress_tonight') is not None:
                return player['progress_tonight']
            return player.get('progress', 0)

        if player_type == 'risers':
            players.sort(
                key=lambda p: (self.get_time_priority(p.get('change_time', '')),
                               -percent_value(p))
            )
        else:
            players.sort(
                key=lambda p: (self.get_time_priority(p.get('change_time', '')),
                               -abs(percent_value(p)))
            )
    
    def format_players_as_string(self, players: List[Dict], player_type: str) -> str:
        """
        将球员列表格式化为字符串
        
        Args:
            players: 球员列表
            player_type: 'risers' 或 'fallers'
            
        Returns:
            格式化的字符串
        """
        if not players:
            return ""
        
        emoji = "📈" if player_type == "risers" else "📉"
        type_text = "即将上涨" if player_type == "risers" else "即将下跌"
        
        result = f"{emoji} {type_text} (共 {len(players)} 人)\n"
        
        for i, player in enumerate(players, 1):
            emoji_text = "🔺" if player_type == "risers" else "🟢"
            result += f"{i}. {emoji_text} {player['name']} ({player['team']}) - {player['position']}\n"
            result += f"   价格: £{player['price']}m | 进度: {player['progress']:+.1f}% | 持有率: {player['ownership']}%"
            
            if player.get('change_time'):
                result += f" | 时间: {player['change_time']}"
            if player.get('progress_tonight') is not None:
                result += f" | 今晚进度: {player['progress_tonight']:+.2f}%"
            result += "\n"
        
        return result
    
    def build_feishu_message(self, analysis: Dict) -> Dict:
        """
        构建飞书消息（单个数据源）
        
        Args:
            analysis: 单个数据源的分析结果
            
        Returns:
            飞书消息体
        """
        source = analysis.get('source', 'Unknown')
        
        # 如果有错误，返回简单消息
        if 'error' in analysis:
            return {
                "msg_type": "text",
                "content": {
                    "text": f"❌ {source} 数据获取失败: {analysis['error']}"
                }
            }
        
        # 构建球员信息字符串
        risers_text = self.format_players_as_string(analysis.get('risers', []), 'risers')
        fallers_text = self.format_players_as_string(analysis.get('fallers', []), 'fallers')
        
        # 组合所有信息
        players_info = ""
        if risers_text:
            players_info += risers_text + "\n"
        if fallers_text:
            players_info += fallers_text
        
        # 筛选规则说明
        filter_rule = ""
        if source in ['ffhub', 'fix']:
            filter_rule = "仅显示2天内变动的球员"
        elif source == 'livefpl':
            filter_rule = "仅显示 progressTonight ±100% 以上"
        
        # 构建消息
        message = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"🏆 FPL 价格变动监控 - {source}",
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": players_info if players_info else "暂无符合条件的球员"
                                }
                            ]
                        ]
                    }
                }
            }
        }
        
        return message
    
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


        
        # 4. 依次发送每个数据源的结果到飞书（只发送有结果的）
        if self.feishu_webhook:
            print("="*80)
            print("📤 开始发送消息到飞书")
            print("="*80)
            
            sent_count = 0
            for analysis in analyses:
                # 只发送有球员结果的数据源
                if analysis.get('risers_count', 0) > 0 or analysis.get('fallers_count', 0) > 0:
                    print(f"📤 发送 {analysis.get('source')} 的结果...")
                    message = self.build_feishu_message(analysis)
                    if self.send_to_feishu(message):
                        sent_count += 1
                    print(message)
                else:
                    print(f"⏭️  跳过 {analysis.get('source')} (无符合条件的球员)")

            if sent_count == 0:
                print("ℹ️  所有数据源都没有符合条件的球员，未发送消息")
            else:
                print(f"✅ 成功发送 {sent_count} 条消息")
        
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

