from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class QCMetric(BaseModel):
    """Single QC metric data point"""
    timestamp: datetime
    metric_name: str
    value: float


class RollingStats(BaseModel):
    """Rolling statistics for a metric"""
    metric_name: str
    window_days: int
    mean: float
    std_dev: float
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    count: int
    updated_at: datetime


class QCAlert(BaseModel):
    """QC threshold violation alert"""
    id: int
    timestamp: datetime
    metric_name: str
    threshold_type: str  # 'upper', 'lower', 'out_of_range'
    threshold_value: float
    actual_value: float
    severity: str  # 'warning', 'critical'
    created_at: datetime
    display_name: Optional[str] = None


class SystemStatus(BaseModel):
    """Overall system health status"""
    status: str  # 'healthy', 'degraded', 'disconnected'
    last_poll: Optional[datetime] = None
    vendor_database: dict
    normalized_database: dict
    total_records_processed: int
    version: str
