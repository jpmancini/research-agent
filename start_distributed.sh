#!/bin/bash
# Launch all workers and the orchestrator as separate processes.
# Each process owns its port and can be restarted independently.
#
# Usage: ./start_distributed.sh
# Stop:  Ctrl-C  (trap below kills all child processes)

set -e

SEARCHER_PORT=8001
REVIEWER_PORT=8002
WRITER_PORT=8003
ORCHESTRATOR_PORT=8000

cleanup() {
    echo "Shutting down..."
    kill 0
}
trap cleanup SIGINT SIGTERM

echo "Starting searcher  on :$SEARCHER_PORT"
WORKER_ROLE=searcher uvicorn workers.app:app --port $SEARCHER_PORT --log-level warning &

echo "Starting reviewer  on :$REVIEWER_PORT"
WORKER_ROLE=reviewer uvicorn workers.app:app --port $REVIEWER_PORT --log-level warning &

echo "Starting writer    on :$WRITER_PORT"
WORKER_ROLE=writer   uvicorn workers.app:app --port $WRITER_PORT   --log-level warning &

# Give workers a moment to bind their ports
sleep 2

echo "Starting orchestrator on :$ORCHESTRATOR_PORT"
SEARCHER_URL=http://localhost:$SEARCHER_PORT \
REVIEWER_URL=http://localhost:$REVIEWER_PORT \
WRITER_URL=http://localhost:$WRITER_PORT \
  uvicorn orchestrator.app:app --port $ORCHESTRATOR_PORT --log-level info &

echo ""
echo "All processes running. Press Ctrl-C to stop."
wait
