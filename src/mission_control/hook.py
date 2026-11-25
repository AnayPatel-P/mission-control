"""Claude Code hook (PostToolUse / Stop). Stdlib only, fails open."""
import json
import os
import sys
import time
from pathlib import Path

PREFIX = "Message from Anay via Mission Control: "


def text_for(nudges) -> str:
    return "\n".join(PREFIX + n["text"] for n in nudges)


def main(kind, stdin, stdout, home=None) -> int:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import inbox

        data = json.loads(stdin.read())
        home = Path(home or os.environ.get("MC_HOME") or Path.home() / ".claude" / "mission-control")
        sid, agent = data["session_id"], data.get("agent_id") or "main"
        marker = home / "stopped" / sid
        if agent == "main" and kind != "stop" and os.path.basename(sid) == sid:
            try:
                os.unlink(marker)  # the session is working again
            except OSError:
                pass
        nudges = inbox.claim(home, sid, agent, time.time())
        if not nudges:
            if kind == "stop" and agent == "main" and os.path.basename(sid) == sid and sid not in (".", ".."):
                inbox._mkdir(home)
                inbox._mkdir(home / "stopped")
                os.close(os.open(marker, os.O_WRONLY | os.O_CREAT, 0o600))  # turn over: the server reports idle
            return 0
        text = text_for(nudges)
        if kind == "stop":
            out = {"decision": "block", "reason": text}
        else:
            out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
        stdout.write(json.dumps(out))
    except BaseException:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "post", sys.stdin, sys.stdout))
