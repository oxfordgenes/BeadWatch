import webbrowser
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config.settings import __version__, BASE_DIR, PORT_RANGE_START, PORT_RANGE_END
from services.application_bootstrap import ApplicationBootstrap
from api.controllers.dashboard_controller import DashboardController
from api.controllers.config_controller import ConfigController
from utils.logger import setup_logger

logger = setup_logger()
bootstrap = ApplicationBootstrap()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic"""
    bootstrap.startup(app)

    yield  # Application runs here

    bootstrap.shutdown(app)


# Create FastAPI app
app = FastAPI(
    title="BeadWatch",
    version=__version__,
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "frontend" / "static"), name="static")

# Note: No CORS middleware needed — frontend is served from the same origin.
# If a future dev tool or external client needs cross-origin access, add
# fastapi.middleware.cors.CORSMiddleware here with explicit allowed origins.


# Register controllers — they access app.state lazily via request.app.state,
# so they work both before and after SQL Server is configured.
dashboard_controller = DashboardController()
app.include_router(dashboard_controller.router)

config_controller = ConfigController()
app.include_router(config_controller.router)


def _require_setup():
    """Return a RedirectResponse to /setup if not configured, else None."""
    return bootstrap.require_setup_redirect(app)


@app.get("/")
async def root():
    """Serve home page or redirect to setup"""
    redirect = _require_setup()
    if redirect:
        return redirect
    return FileResponse(BASE_DIR / "frontend" / "home.html")


@app.get("/dashboard")
async def dashboard_page():
    """Serve reagent QC dashboard"""
    redirect = _require_setup()
    if redirect:
        return redirect
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/instruments")
async def instruments_page():
    """Serve instrument comparison page"""
    redirect = _require_setup()
    if redirect:
        return redirect
    return FileResponse(BASE_DIR / "frontend" / "instruments.html")


@app.get("/operators")
async def operators_page():
    """Serve operator comparison page"""
    redirect = _require_setup()
    if redirect:
        return redirect
    return FileResponse(BASE_DIR / "frontend" / "operators.html")


@app.get("/settings")
async def settings_page():
    """Serve settings page"""
    redirect = _require_setup()
    if redirect:
        return redirect
    return FileResponse(BASE_DIR / "frontend" / "settings.html")


@app.get("/disclaimer")
async def disclaimer_page():
    """Serve disclaimer and license page"""
    return FileResponse(BASE_DIR / "frontend" / "disclaimer.html")


@app.get("/setup")
async def setup_page():
    """Serve configuration wizard"""
    return FileResponse(BASE_DIR / "frontend" / "setup.html")

@app.get("/health")
async def health():
    """Health/readiness check. Also serves version to the frontend
    without requiring SQL Server to be configured."""
    return {"status": "ok", "version": __version__}


def main():
    """Entry point for the application"""
    import uvicorn

    import threading
    import time
    from urllib.request import urlopen

    # Try ports in range to avoid TOCTOU issues with pre-binding.
    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        url = f"http://localhost:{port}"

        logger.info(f"Starting server on {url}")
        print(f"\n{'='*50}")
        print(f"  BeadWatch v{__version__}")
        print(f"  Dashboard: {url}")
        print(f"{'='*50}\n")

        # Open browser once health endpoint is reachable.
        # Default arg binds url eagerly so retries on the next port
        # don't leave a stale thread polling the wrong address.
        def open_browser_when_ready(url=url):
            """Poll until HTTP health endpoint responds, then open browser."""
            for _ in range(30):  # Up to 15 seconds
                try:
                    with urlopen(f"{url}/health", timeout=1) as resp:
                        if resp.status == 200:
                            webbrowser.open(url)
                            return
                except Exception:
                    time.sleep(0.5)

        threading.Thread(target=open_browser_when_ready, daemon=True).start()

        try:
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(config)
            server.run()
            break
        except OSError as e:
            # Port was likely taken between checks; try the next one.
            logger.warning(f"Port {port} unavailable: {e}")
            continue
    else:
        raise RuntimeError(f"No available ports found in range {PORT_RANGE_START}-{PORT_RANGE_END}")


if __name__ == "__main__":
    main()
