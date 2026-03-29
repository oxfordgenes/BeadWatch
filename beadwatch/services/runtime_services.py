from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class RuntimeServices:
    sqlite_db: Optional[Any] = None
    vendor_db: Optional[Any] = None
    normalized_db: Optional[Any] = None
    polling_service: Optional[Any] = None
    scheduler: Optional[Any] = None


RUNTIME_STATE_KEY = "runtime_services"


def get_runtime(app) -> RuntimeServices:
    runtime = getattr(app.state, RUNTIME_STATE_KEY, None)
    if runtime is None:
        runtime = RuntimeServices(
            sqlite_db=getattr(app.state, "sqlite_db", None),
            vendor_db=getattr(app.state, "vendor_db", None),
            normalized_db=getattr(app.state, "normalized_db", None),
            polling_service=getattr(app.state, "polling_service", None),
            scheduler=getattr(app.state, "scheduler", None),
        )
        setattr(app.state, RUNTIME_STATE_KEY, runtime)
    return runtime


def set_runtime_field(app, field_name: str, value: Any) -> None:
    runtime = get_runtime(app)
    setattr(runtime, field_name, value)
    setattr(app.state, field_name, value)
