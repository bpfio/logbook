import json
import os
import sys
import time


def main() -> None:
    delay = float(os.getenv("SLOW_MCP_DELAY", "5.2"))
    pid_file = os.getenv("SLOW_MCP_PID_FILE")
    if pid_file:
        with open(pid_file, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    for line in sys.stdin:
        message = json.loads(line)
        time.sleep(delay)
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"slow": True, "delay": delay},
        }
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
