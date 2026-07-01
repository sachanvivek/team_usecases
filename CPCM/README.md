# Agent Orchestrator

This repository contains an agent orchestration pipeline using [LangGraph](https://github.com/langchain-ai/langgraph) and [Poetry](https://python-poetry.org/) to manage and run multiple AI agents in a structured and modular workflow.

## Features

- Modular agent architecture (e.g., MetricCollectorAgent, ForecastingAgent)
- Forecasting logic using multiple ML models (ARIMA, SARIMA, XGBoost, etc.)
- Forecast results persisted to a relational database
- Built-in FastAPI server for triggering orchestration via API

## Prerequisites

- Python 3.11
- Poetry
- MySQL or compatible database configured

## Installation

```bash
git clone <your-repo-url>
cd agent_orch
poetry install
```

## Configuration

- Ensure your DB credentials and connection logic are correctly set in `agent_orch/utils/db.py`.

## Running the API Server

```bash
poetry run python main.py
```

## API Usage

### Health Check

```http
GET /
```

### Run Full Pipeline

```http
POST /pipeline
Content-Type: application/json

{
  "server_id": "test",
  "resource_type": "cpu"
}
```

## Project Structure

```
agent_orch/
│
├── agents/
│   ├── base_agent.py
│   ├── forecasting.py
│   ├── metric_collector.py
│   └── models/
│       └── models.py
│
├── graph_builder.py
├── main.py
├── pyproject.toml
└── README.md
```

## Notes

- Forecast results for only the top 3 selected models will be stored in the database.
- You can extend the pipeline by adding more agents to the `graph_builder.py` file.

## License

MIT
