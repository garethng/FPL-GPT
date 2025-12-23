# FPL-GPT Project Context

## Overview
FPL-GPT is a comprehensive system for analyzing Fantasy Premier League (FPL) data and exposing it to Large Language Models (LLMs) via the Model Context Protocol (MCP). It includes data fetching, storage in Supabase, price monitoring, and an API server.

## Key Components

### 1. Core Library (`fpl/`)
*   **Description:** A local copy/fork of the `amosbastian/fpl` Python library.
*   **Purpose:** Provides a wrapper around the official FPL API to fetch raw data (players, teams, fixtures, gameweeks).
*   **Usage:** Used by the Data Loader and potentially other components to interact with FPL endpoints.

### 2. Data Loader (`fpl_data_loader/`)
*   **Description:** A background service responsible for keeping the database up-to-date.
*   **Technology:** Python, Docker, Cron.
*   **Functionality:**
    *   Runs on a schedule (daily at 9 AM Beijing time).
    *   Fetches latest data from FPL API.
    *   Fetches prediction data from external sources (e.g., Fantasy Football Hub).
    *   Stores/Updates data in **Supabase**.
*   **Key File:** `fpl_data_loader/main.py`

### 3. MCP Server (`mcp_server/`)
*   **Description:** An API server implementing the Model Context Protocol (MCP).
*   **Technology:** Python, `mcp` library, `FastAPI` (via `FastMCP`).
*   **Purpose:** Allows LLMs (like Claude or Gemini) to query FPL data contextually.
*   **Data Source:** Reads directly from Supabase.
*   **Tools Provided:** Likely exposes tools for querying player stats, fixture difficulty, and point predictions.
*   **Key File:** `mcp_server/main.py`

### 4. Price Monitor (`fpl_price_monitor/`)
*   **Description:** A utility to monitor and notify about player price changes.
*   **Functionality:**
    *   Aggregates price predictions from multiple sources (FFHub, Fix, LiveFPL).
    *   Sends notifications via Feishu (Lark) Webhook.
    *   Can track specific players from a user's FPL Team ID.
*   **Key File:** `fpl_price_monitor/fetch_and_notify.py`

### 5. Database & Infrastructure
*   **Database:** **Supabase** (PostgreSQL) is the primary data store.
    *   (Legacy/Transition) SQLite support exists but Supabase is preferred in newer code.
*   **Migration:** `supbase/` contains scripts to migrate data from SQLite to Supabase.
*   **Docker:** `docker-compose.yml` orchestrates the `fpl-data-loader` and `mcp-server` services.

## Setup & Configuration

### Environment Variables (`.env`)
The project requires a `.env` file with the following keys:
```bash
FPL_EMAIL=your_email@example.com
FPL_PASSWORD=your_password
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
FEISHU_WEBHOOK=your_feishu_webhook_url # For price monitor
FPL_TEAM_ID=your_team_id # For price monitor specific team tracking
```

### Running with Docker
```bash
docker-compose up -d
```
*   Starts `fpl-data-loader` (background cron) and `mcp-server` (exposed on port 8000).

### Development
*   **Language:** Python 3.12+
*   **Dependencies:** Managed via `requirements.txt` in each component folder.
*   **Local Run (Data Loader):** `python fpl_data_loader/main.py`
*   **Local Run (Price Monitor):** `python fpl_price_monitor/fetch_and_notify.py`

## Architecture Notes
*   **Data Flow:** FPL API -> Data Loader -> Supabase -> MCP Server -> LLM Client.
*   **Authentication:** FPL credentials are used for data fetching; Supabase keys are used for storage/retrieval.
*   **Localization:** Comments and some docs are in Chinese; Timezone set to Asia/Shanghai.
