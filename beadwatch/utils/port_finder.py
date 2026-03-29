import socket
from config.settings import PORT_RANGE_START, PORT_RANGE_END


def find_available_port() -> int:
    """Find first available port in configured range by attempting to bind.

    Note: use only for diagnostics/tests. For runtime server startup,
    prefer binding in the uvicorn loop to avoid TOCTOU races.
    """
    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports found in range {PORT_RANGE_START}-{PORT_RANGE_END}")
