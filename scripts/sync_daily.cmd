@echo off
rem Daily knowledge-base sync for Visa Policy Chat (CLAUDE.md: sync runs daily on a schedule).
rem Fetches every source, replaces changed sections, tags court orders, then runs the free
rem retrieval eval. No Claude API calls, so it uses no credits. Output: data\sync-run.log;
rem one JSON record per run: data\sync-log.jsonl.
cd /d "%~dp0.."
echo ===== %date% %time% >> data\sync-run.log
".venv\Scripts\python.exe" -m ingest.sync --eval >> data\sync-run.log 2>&1
echo exit code %errorlevel% >> data\sync-run.log
