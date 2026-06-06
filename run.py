"""
CLI entry point.

Usage:
    python run.py "What are the latest efforts to simulate a brain in a computer?"
    python run.py "Your question" --session-id <id>   # resume a prior run
"""
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
import httpx


OUTPUT_DIR = Path("output")


def _save_brief(session_id: str, question: str, data: dict) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"{timestamp}_{session_id[:8]}.md"
    path.write_text(
        f"# Research Brief\n\n"
        f"**Question:** {question}\n"
        f"**Session:** {session_id}\n"
        f"**Steps completed:** {data['steps_completed']}\n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"---\n\n"
        f"{data['brief']}\n\n"
        + (
            "---\n\n## Open Questions\n\n"
            + "\n".join(f"- {q}" for q in data["open_questions"])
            if data["open_questions"] else ""
        )
    )
    return path


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

        path = _save_brief(data["session_id"], args.question, data)

        print(f"\n{'═' * 60}")
        print(f"SESSION ID:      {data['session_id']}")
        print(f"STEPS COMPLETED: {data['steps_completed']}")
        print(f"SAVED TO:        {path}")
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
