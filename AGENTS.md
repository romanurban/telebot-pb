# Telebot Agent Guide

This guide explains how to get started with the project and run tests.

## Project Overview
- Async Telegram bot using `aiogram`.
- Integrates with the OpenAI Assistants API.
- Main logic is in `main.py`.
- Tests are located in the `tests/` directory and cover utility functions.

## Setup
1. **Install dependencies**
   - Using `pip` directly works well in this environment:
     ```bash
     pip install -e .
     ```
     The project requires Python 3.13 or later.
   - For development and running tests, include the `[dev]` extras:
     ```bash
     pip install -e .[dev]
     ```
   - Alternatively, you may use `uv pip sync` if `uv` is available.
2. **Create an environment file**
   - Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN` and `OPENAI_API_KEY`.
   - The `.env` file is ignored by git; never commit it.

## Running the Bot
Execute:
```bash
python main.py
```
Make sure the `.env` file contains valid keys before running.

## Running Tests
1. **Install dependencies** (if you haven't already):
   ```bash
   pip install -e .[dev]
   ```
   The `[dev]` extras install `pytest` and `pytest-asyncio`, which are required for all tests to run. You only need to do this once per environment.
2. **Run the tests** from the repository root:
   ```bash
   pytest -q
   ```
   All tests should pass. The test suite sets minimal environment variables automatically.
   Async tests use the `pytest-asyncio` plugin, which is included in the `dev` optional dependencies, so running `pytest` works for both sync and async tests.

