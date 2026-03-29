from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal
from datetime import datetime


class CredentialsInput(BaseModel):
    """Input model for database credentials"""
    server: str = Field(..., description="SQL Server address")
    vendor_database: str = Field(..., description="Vendor database name")
    username: str = Field(..., description="SQL Server username")
    password: str = Field(..., description="SQL Server password")


class CredentialsStored(BaseModel):
    """Stored credentials (password encrypted)"""
    server: str
    vendor_database: str
    normalized_database: str
    username: str
    password: str  # Decrypted when retrieved


class ConfigStatus(BaseModel):
    """Configuration status response"""
    configured: bool
    server: Optional[str] = None
    vendor_database: Optional[str] = None
    normalized_database: Optional[str] = None
    username: Optional[str] = None
    normalized_initialized: bool = False
    created_at: Optional[datetime] = None


class ConnectionTestResult(BaseModel):
    """Result of a connection test"""
    success: bool
    message: str
    details: Optional[dict] = None


class PollingIntervalInput(BaseModel):
    """Input model for polling interval configuration"""
    interval_minutes: int = Field(
        ...,
        ge=5,
        le=1440,
        description="Polling interval in minutes (5–1440)"
    )


class QCSampleDefinitionInput(BaseModel):
    """Input model for creating a QC sample definition"""
    pattern: str = Field(..., min_length=1, max_length=50)
    match_type: Literal['substring', 'exact'] = 'substring'
    role: Literal['positive', 'negative']
    label: Optional[str] = None


class QCSampleDefinition(BaseModel):
    """Stored QC sample definition"""
    id: int
    pattern: str
    match_type: str
    role: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None


class QCTrackedBeadInput(BaseModel):
    """Input model for creating a tracked bead"""
    catalog_group: str = Field(..., min_length=1, max_length=20)
    bead_lot: str = Field(..., min_length=1, max_length=20)
    bead_id: str = Field(..., min_length=1, max_length=20)
    label: Optional[str] = None


class QCTrackedBead(BaseModel):
    """Stored tracked bead"""
    id: int
    catalog_group: str
    bead_lot: str
    bead_id: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None


class ExcludedInstrumentInput(BaseModel):
    """Input model for excluding an instrument serial number"""
    serial_number: str = Field(..., min_length=1, max_length=50)
    label: Optional[str] = None


class ExcludedInstrument(BaseModel):
    """Stored excluded instrument"""
    id: int
    serial_number: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None


class InstrumentAliasInput(BaseModel):
    """Input model for assigning a display name to an instrument serial number"""
    serial_number: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=50)


class InstrumentAlias(BaseModel):
    """Stored instrument alias"""
    id: int
    serial_number: str
    display_name: str
    created_at: Optional[datetime] = None


class ExcludedOperatorInput(BaseModel):
    """Input model for excluding an operator username"""
    username: str = Field(..., min_length=1, max_length=100)
    label: Optional[str] = None


class ExcludedOperator(BaseModel):
    """Stored excluded operator"""
    id: int
    username: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None


class OperatorAliasInput(BaseModel):
    """Input model for assigning a display name to an operator username"""
    username: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=50)


class OperatorAlias(BaseModel):
    """Stored operator alias"""
    id: int
    username: str
    display_name: str
    created_at: Optional[datetime] = None


class QCThreshold(BaseModel):
    """A single QC alert threshold row from the normalized DB."""
    id: int
    metric_name: str
    upper_warning: Optional[float] = None
    upper_critical: Optional[float] = None
    lower_warning: Optional[float] = None
    lower_critical: Optional[float] = None
    enabled: bool = True


class QCThresholdUpdateInput(BaseModel):
    """Input model for updating a threshold row.

    Per-side ordering invariants:
      - lower_critical <= lower_warning  (when both provided)
      - upper_warning  <= upper_critical (when both provided)

    Cross-side ordering (lower_warning <= upper_warning) is only
    enforced when all four values are present for the same metric.
    """
    upper_warning: Optional[float] = None
    upper_critical: Optional[float] = None
    lower_warning: Optional[float] = None
    lower_critical: Optional[float] = None
    enabled: bool = True

    @model_validator(mode='after')
    def check_threshold_ordering(self) -> 'QCThresholdUpdateInput':
        lc, lw = self.lower_critical, self.lower_warning
        uw, uc = self.upper_warning, self.upper_critical
        # Per-side: critical must be more extreme than warning
        if lc is not None and lw is not None and lc > lw:
            raise ValueError('lower_critical must be <= lower_warning')
        if uw is not None and uc is not None and uw > uc:
            raise ValueError('upper_warning must be <= upper_critical')
        # Cross-side: only enforce when both sides are fully specified
        if (lc is not None and lw is not None and
                uw is not None and uc is not None and lw > uw):
            raise ValueError('lower_warning must be <= upper_warning when all four thresholds are set')
        return self
