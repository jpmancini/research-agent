"""
CLI entry point.

Usage:
    python run.py "What are the latest efforts to simulate a brain in a computer?"
    python run.py "Your question" --session-id <id>   # resume a prior run
"""
import asyncio
import argparse
import json
from pathlib import Path
from datetime import datetime
import httpx

OUTPUT_DIR = Path("output")
NODE_ICONS = {"search": "[search]", "review": "[review]", "write": "[write]"}


def _save_brief(session_id: str, question: str, brief: str, open_questions: list[str]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"{timestamp}_{session_id[:8]}.md"
    path.write_text(
        f"# Research Brief\n\n"
        f"**Question:** {question}\n"
        f"**Session:** {session_id}\n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"---\n\n{brief}\n\n"
        + ("---\n\n## Open Questions\n\n" + "\n".join(f"- {q}" for q in open_questions)
           if open_questions else "")
    )
    return path


def main():
    parser = argparse.ArgumentParser(description="Run a research agent query")
    parser.add_argument("question", help="The research question to investigate")
    parser.add_argument("--session-id", default=None, help="Resume a prior session")
    parser.add_argument("--host", default="http://localhost:8000")
    args = parser.parse_args()

    async def run():
        payload = {"research_question": args.question}
        if args.session_id:
            payload["session_id"] = args.session_id

        print(f"\nResearching: {args.question}\n{'-' * 60}")

        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", f"{args.host}/orchestrate/stream", json=payload) as resp:
                resp.raise_for_status()
                session_id = None
                brief = ""
                open_questions = []

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    etype = event.get("type")

                    if etype == "plan_created":
                        session_id = event["session_id"]
                        nodes = event["nodes"]
                        print(f"\nSession: {session_id}")
                        print(f"\nInitial plan ({len(nodes)} nodes):")
                        for n in nodes:
                            deps = f"  <- {', '.join(n['depends_on'])}" if n["depends_on"] else ""
                            print(f"  {NODE_ICONS.get(n['type'], '[node]')} {n['id']}{deps}")
                        print()

                    elif etype == "node_dispatched":
                        icon = NODE_ICONS.get(event["node_type"], "-")
                        print(f"  {icon}  dispatching  {event['node_id']}")
                        print(f"     task: {event['task']}")

                    elif etype == "node_done":
                        icon = NODE_ICONS.get(event["node_type"], "-")
                        conf = event["confidence"]
                        qual = event.get("quality", 0.0)
                        print(f"  {icon}  done         {event['node_id']}  (confidence: {conf:.0%}  quality: {qual:.0%})")
                        print(f"     {event['findings']}")

                    elif etype == "nodes_injected":
                        print(f"\n  + injected {event['count']} review nodes:")
                        for url in event["urls"]:
                            print(f"    - {url[:70]}")
                        print()

                    elif etype == "node_failed":
                        print(f"  [failed]  {event['node_id']}  [{event['failure_type']}]")
                        print(f"     {event['reason']}")

                    elif etype == "complete":
                        brief = event.get("brief", "")
                        open_questions = event.get("open_questions", [])
                        steps = event.get("steps_completed", 0)

                        path = _save_brief(session_id, args.question, brief, open_questions)
                        print(f"\n{'=' * 60}")
                        print(f"STEPS COMPLETED: {steps}")
                        print(f"SAVED TO:        {path}")
                        print(f"\n{'-' * 60}\nBRIEF\n{'-' * 60}\n")
                        print(brief)

                        if open_questions:
                            print(f"\n{'-' * 60}\nOPEN QUESTIONS\n{'-' * 60}")
                            for q in open_questions:
                                print(f"  - {q}")

                        print(f"\n{'=' * 60}")
                        print(f"To resume: python run.py \"{args.question}\" --session-id {session_id}")

                    elif etype == "error":
                        print(f"\n  ERROR: {event.get('message', 'unknown error')}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
