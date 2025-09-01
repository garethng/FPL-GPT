#!/usr/bin/env python3

import requests
import json
import concurrent.futures
import time
from typing import List, Dict

def fetch_page(league_id: int, page: int) -> List[Dict]:
    """Fetch a single page of league standings"""
    url = f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/?page_new_entries=1&page_standings={page}&phase=1"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data['standings']['results']
        else:
            print(f"Error fetching page {page}: HTTP {response.status_code}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching page {page}: {e}")
        return []

def get_league_standings_sorted_by_gw3(league_id=65, max_pages=500, max_workers=10):
    """Get league standings sorted by GW3 points using multithreading"""
    all_players = []
    
    # First, get the first page to determine total pages
    first_page = fetch_page(league_id, 1)
    if not first_page:
        return []
    
    all_players.extend(first_page)
    print(f"Fetched page 1 with {len(first_page)} players")
    
    # Use ThreadPoolExecutor for concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit tasks for remaining pages
        future_to_page = {
            executor.submit(fetch_page, league_id, page): page 
            for page in range(2, max_pages + 1)
        }
        
        # Process completed tasks
        for future in concurrent.futures.as_completed(future_to_page):
            page = future_to_page[future]
            try:
                players = future.result()
                if players:
                    all_players.extend(players)
                    print(f"Fetched page {page} with {len(players)} players")
                else:
                    print(f"No players found on page {page}, stopping pagination")
                    break
            except Exception as e:
                print(f"Error processing page {page}: {e}")
    
    # Sort by GW3 points (event_total) descending
    sorted_players = sorted(all_players, key=lambda x: x['event_total'], reverse=True)
    
    return sorted_players

def save_all_scores(players: List[Dict], league_id: int):
    """Save all user scores to comprehensive files"""
    # Save detailed CSV with all information
    csv_filename = f'league_{league_id}_all_scores.csv'
    with open(csv_filename, 'w', encoding='utf-8') as f:
        f.write("Rank,GW3_Rank,Player_Name,Team_Name,GW3_Points,Total_Points,Entry_ID\n")
        for player in players:
            f.write(f"{player['rank']},{player['last_rank'] or 'N/A'},"
                   f"\"{player['player_name']}\",\"{player['entry_name']}\","
                   f"{player['event_total']},{player['total']},{player['entry']}\n")
    
    # Save sorted by GW3 points
    gw3_filename = f'league_{league_id}_gw3_sorted.txt'
    with open(gw3_filename, 'w', encoding='utf-8') as f:
        f.write(f"League {league_id} - All Users Sorted by GW3 Points\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'GW3 Rank':<8} {'Overall Rank':<8} {'Player Name':<25} {'Team Name':<25} {'GW3 Points':<12} {'Total Points':<12}\n")
        f.write("-" * 100 + "\n")
        
        for i, player in enumerate(players, 1):
            f.write(f"{i:<8} {player['rank']:<8} {player['player_name'][:24]:<25} {player['entry_name'][:24]:<25} {player['event_total']:<12} {player['total']:<12}\n")
    
    # Save JSON with all data
    json_filename = f'league_{league_id}_complete_data.json'
    with open(json_filename, 'w', encoding='utf-8') as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
    
    return csv_filename, gw3_filename, json_filename

def main():
    league_id = 65
    print(f"Getting league {league_id} standings sorted by GW3 points...")
    print("Using multithreading for faster data fetching...")
    
    start_time = time.time()
    players = get_league_standings_sorted_by_gw3(league_id, max_workers=20)
    end_time = time.time()
    
    print(f"\nFetched {len(players)} users in {end_time - start_time:.2f} seconds")
    
    if not players:
        print("No players found!")
        return
    
    # Display top 50
    print(f"\nTop 50 Users in League {league_id} by GW3 Points:")
    print("=" * 100)
    print(f"{'GW3 Rank':<8} {'Overall Rank':<8} {'Player Name':<25} {'Team Name':<25} {'GW3 Points':<12} {'Total Points':<12}")
    print("-" * 100)
    
    for i, player in enumerate(players[:50], 1):
        print(f"{i:<8} {player['rank']:<8} {player['player_name'][:24]:<25} {player['entry_name'][:24]:<25} {player['event_total']:<12} {player['total']:<12}")
    
    # Save all scores to files
    csv_file, gw3_file, json_file = save_all_scores(players, league_id)
    
    print(f"\nResults saved to:")
    print(f"- Detailed CSV: {csv_file}")
    print(f"- GW3 Sorted: {gw3_file}")
    print(f"- Complete JSON: {json_file}")
    
    # Show some statistics
    total_users = len(players)
    avg_gw3 = sum(p['event_total'] for p in players) / total_users
    max_gw3 = max(p['event_total'] for p in players)
    min_gw3 = min(p['event_total'] for p in players)
    
    print(f"\nLeague Statistics:")
    print(f"Total Users: {total_users}")
    print(f"Average GW3 Score: {avg_gw3:.1f}")
    print(f"Highest GW3 Score: {max_gw3}")
    print(f"Lowest GW3 Score: {min_gw3}")

if __name__ == "__main__":
    main()