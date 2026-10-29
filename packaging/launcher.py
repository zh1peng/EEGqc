"""Frozen desktop entry point; handle spawned workers before importing Qt."""

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-release":
        try:
            from eegqc.release_check import main

            raise SystemExit(main(sys.argv[2:]))
        except Exception:
            # A missing frozen dependency must produce an actionable CI report,
            # including failures before the scientific acceptance module imports.
            import json
            from pathlib import Path
            import traceback

            output = Path(sys.argv[sys.argv.index("--output") + 1])
            output.mkdir(parents=True, exist_ok=True)
            name = "reopen.json" if "reopen" in sys.argv else "acceptance.json"
            (output / name).write_text(
                json.dumps({"status": "failed", "frozen": True, "error": traceback.format_exc()}, indent=2),
                encoding="utf-8",
            )
            raise SystemExit(1)
    from eegqc.__main__ import main

    raise SystemExit(main())
