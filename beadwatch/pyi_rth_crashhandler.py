"""PyInstaller runtime hook — installs a crash handler that keeps the
console open and writes to beadwatch.log on unhandled exceptions."""
import sys
import traceback
from pathlib import Path


def _excepthook(exc_type, exc_value, exc_tb):
    msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # Write to log next to executable
    try:
        if getattr(sys, 'frozen', False):
            log_path = Path(sys.executable).parent / "beadwatch.log"
        else:
            log_path = Path(__file__).parent / "beadwatch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write("CRASH\n")
            f.write(msg)
            f.write(f"{'='*60}\n")
    except Exception:
        pass
    print(msg, file=sys.stderr)
    print("\nBeadWatch failed to start. Details written to beadwatch.log", file=sys.stderr)
    try:
        input("Press Enter to exit.")
    except EOFError:
        pass


sys.excepthook = _excepthook
