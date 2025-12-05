#!/usr/bin/env python3
"""
展示飞书消息的 JSON 格式
"""

import json
import os


def main():
    json_file = "fpl_price_analysis.json"

    if not os.path.exists(json_file):
        print(f"❌ 未找到分析结果文件: {json_file}")
        print("请先运行: python fetch_and_notify.py")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print("\n" + "=" * 80)
    print("📱 飞书消息 JSON 格式示例")
    print("=" * 80)

    for analysis in data.get("analyses", []):
        source = analysis.get("source", "Unknown")
        risers = analysis.get("risers_count", 0)
        fallers = analysis.get("fallers_count", 0)

        if risers == 0 and fallers == 0:
            print(f"\n⏭️  跳过 {source} (无符合条件的球员)")
            continue

        print(f"\n{'=' * 80}")
        print(f"📊 数据源: {source}")
        print(f"   上涨: {risers} 人 | 下跌: {fallers} 人")
        print(f"{'=' * 80}\n")

        monitor = __import__("fetch_and_notify").FPLPriceMonitor()
        message = monitor.build_feishu_message(analysis)
        print(json.dumps(message, ensure_ascii=False, indent=2))
        print()

    print("\n" + "=" * 80)
    print("💡 说明:")
    print("   - msg_type: 'post' 表示富文本消息")
    print("   - 球员信息在 content.post.zh_cn.content 末尾的 text 字段")
    print("   - 所有球员信息作为一个格式化的字符串，便于解析")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()

