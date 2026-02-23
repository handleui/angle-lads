# Run both Python backend and Vite dev server
dev:
    #!/usr/bin/env bash
    trap 'kill 0' EXIT
    uv run main.py &
    npm run dev &
    wait

# Run only the Python backend
py:
    uv run main.py

# Run only the Vite dev server
web:
    npm run dev

# Lint everything
lint:
    ruff check .
    npx @biomejs/biome check .

# Format everything
fmt:
    ruff format .
    ruff check --fix .
    npx @biomejs/biome check --write .

# Install all dependencies
setup:
    uv sync
    npm install
