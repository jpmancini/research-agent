"""
CLI entry point.

Usage:
    python run.py "What are the latest efforts to simulate a brain in a computer?"
    python run.py "Your question" --session-id <id>   # resume a prior run
"""
import sys
import asyncio
import argparse
import httpx


def main():
    parser = argparse.ArgumentParser(description="Run a research agent query")
    parser.add_argument("question", help="The research question to investigate")
    parser.add_argument("--session-id", default=None, help="Resume a prior session")
    parser.add_argument("--host", default="http://localhost:8000", help="API host")
    args = parser.parse_args()

    async def run():
        payload = {"research_question": args.question}
        if args.session_id:
            payload["session_id"] = args.session_id

        print(f"\nResearching: {args.question}\n{'─' * 60}")

        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(f"{args.host}/orchestrate", json=payload)
            resp.raise_for_status()
            data = resp.json()

        print(f"\n{'═' * 60}")
        print(f"SESSION ID: {data['session_id']}")
        print(f"STEPS COMPLETED: {data['steps_completed']}")
        print(f"\n{'─' * 60}\nBRIEF\n{'─' * 60}\n")
        print(data["brief"])

        if data["open_questions"]:
            print(f"\n{'─' * 60}\nOPEN QUESTIONS\n{'─' * 60}")
            for q in data["open_questions"]:
                print(f"  • {q}")

        print(f"\n{'═' * 60}")
        print(f"To resume: python run.py \"{args.question}\" --session-id {data['session_id']}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
