-- BeadWatch Normalized Database Schema
-- Version: 1.0.0
-- All statements are idempotent (IF NOT EXISTS guards) so the script
-- is safe to re-run against an existing database.

-- Metadata tracking table
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'ProcessedRecords')
CREATE TABLE ProcessedRecords (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor_source VARCHAR(50) NOT NULL,
    vendor_record_uuid VARCHAR(100) NOT NULL,
    vendor_record_timestamp DATETIME NOT NULL,
    processed_at DATETIME DEFAULT GETDATE(),
    CONSTRAINT UQ_VendorRecord UNIQUE (vendor_source, vendor_record_uuid)
);
GO

-- Calculated QC metrics cache
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'QCMetrics')
CREATE TABLE QCMetrics (
    id INT IDENTITY(1,1) PRIMARY KEY,
    processed_record_id INT NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT,
    calculated_at DATETIME DEFAULT GETDATE(),
    CONSTRAINT FK_QCMetrics_ProcessedRecords
        FOREIGN KEY (processed_record_id) REFERENCES ProcessedRecords(id)
);
GO

-- Rolling statistics
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'RollingStats')
CREATE TABLE RollingStats (
    id INT IDENTITY(1,1) PRIMARY KEY,
    metric_name VARCHAR(100) NOT NULL,
    window_days INT NOT NULL,
    mean_value FLOAT,
    std_dev FLOAT,
    min_value FLOAT,
    max_value FLOAT,
    record_count INT,
    updated_at DATETIME DEFAULT GETDATE(),
    CONSTRAINT UQ_RollingStats UNIQUE (metric_name, window_days)
);
GO

-- QC threshold violations
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'QCAlerts')
CREATE TABLE QCAlerts (
    id INT IDENTITY(1,1) PRIMARY KEY,
    processed_record_id INT NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    threshold_type VARCHAR(50),
    threshold_value FLOAT,
    actual_value FLOAT,
    severity VARCHAR(20),
    acknowledged BIT DEFAULT 0,
    acknowledged_at DATETIME NULL,
    created_at DATETIME DEFAULT GETDATE(),
    CONSTRAINT FK_QCAlerts_ProcessedRecords
        FOREIGN KEY (processed_record_id) REFERENCES ProcessedRecords(id)
);
GO

-- QC threshold configuration
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'QCThresholds')
BEGIN
    CREATE TABLE QCThresholds (
        id INT IDENTITY(1,1) PRIMARY KEY,
        metric_name VARCHAR(100) NOT NULL,
        upper_warning FLOAT NULL,
        upper_critical FLOAT NULL,
        lower_warning FLOAT NULL,
        lower_critical FLOAT NULL,
        enabled BIT DEFAULT 1,
        created_at DATETIME DEFAULT GETDATE(),
        updated_at DATETIME DEFAULT GETDATE(),
        CONSTRAINT UQ_QCThresholds_MetricName UNIQUE (metric_name)
    );

    -- Default threshold values (only inserted with the table)
    INSERT INTO QCThresholds (metric_name, upper_warning, upper_critical, lower_warning, lower_critical)
    VALUES
        ('min_bead_count', NULL, NULL, 50, 25),
        ('mean_mfi', 20000, 25000, 100, 50),
        ('signal_to_noise', NULL, NULL, 10, 5),
        ('negative_control_mfi', 500, 1000, NULL, NULL);
END;
GO

-- Migration: add display_name to ProcessedRecords for human-readable alert identifiers
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('dbo.ProcessedRecords') AND name = 'display_name')
    ALTER TABLE ProcessedRecords ADD display_name VARCHAR(200) NULL;
GO

-- Migration: allow QCAlerts without a ProcessedRecord (QC sample outlier alerts)
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.QCAlerts') AND name = 'processed_record_id' AND is_nullable = 0)
    ALTER TABLE QCAlerts ALTER COLUMN processed_record_id INT NULL;
GO
-- Migration: add display_name and qc_cache_id to QCAlerts for QC sample outlier alerts
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('dbo.QCAlerts') AND name = 'display_name')
    ALTER TABLE QCAlerts ADD display_name VARCHAR(200) NULL;
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('dbo.QCAlerts') AND name = 'qc_cache_id')
    ALTER TABLE QCAlerts ADD qc_cache_id INT NULL;
GO

-- Performance indexes (IF NOT EXISTS avoids errors on re-run)
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ProcessedRecords_Timestamp' AND object_id = OBJECT_ID('dbo.ProcessedRecords'))
    CREATE INDEX IX_ProcessedRecords_Timestamp ON ProcessedRecords(vendor_record_timestamp);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_ProcessedRecords_Source' AND object_id = OBJECT_ID('dbo.ProcessedRecords'))
    CREATE INDEX IX_ProcessedRecords_Source ON ProcessedRecords(vendor_source);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCMetrics_MetricName' AND object_id = OBJECT_ID('dbo.QCMetrics'))
    CREATE INDEX IX_QCMetrics_MetricName ON QCMetrics(metric_name);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCMetrics_RecordId' AND object_id = OBJECT_ID('dbo.QCMetrics'))
    CREATE INDEX IX_QCMetrics_RecordId ON QCMetrics(processed_record_id);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCAlerts_CreatedAt' AND object_id = OBJECT_ID('dbo.QCAlerts'))
    CREATE INDEX IX_QCAlerts_CreatedAt ON QCAlerts(created_at);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCAlerts_Severity' AND object_id = OBJECT_ID('dbo.QCAlerts'))
    CREATE INDEX IX_QCAlerts_Severity ON QCAlerts(severity, acknowledged);
GO

-- Migration: drop QCSampleCache if tray_id has wrong type (INT instead of NVARCHAR).
-- The table is a disposable cache that gets lazily repopulated from the vendor DB.
-- NOTE: must join on user_type_id (not system_type_id) because nvarchar and sysname
-- share the same system_type_id, which caused this migration to fire on every startup.
IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON c.user_type_id = t.user_type_id
    WHERE c.object_id = OBJECT_ID('dbo.QCSampleCache')
      AND c.name = 'tray_id'
      AND t.name != 'nvarchar'
)
    DROP TABLE dbo.QCSampleCache;
GO

-- QC Sample Cache: pre-computed metrics from vendor DB, refreshed incrementally
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'QCSampleCache')
CREATE TABLE QCSampleCache (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    catalog_group   NVARCHAR(50)  NOT NULL,
    bead_lot        NVARCHAR(50)  NOT NULL,
    tray_id         NVARCHAR(200) NOT NULL,
    sample_id_name  NVARCHAR(200),
    patient_id      NVARCHAR(200),
    analysis_dt     DATETIME2     NOT NULL,
    run_name        NVARCHAR(200),
    display_name    NVARCHAR(200),
    role            NVARCHAR(20)  NOT NULL,
    instrument      NVARCHAR(200),
    pc              FLOAT,
    nc              FLOAT,
    median_mfi      FLOAT,
    median_count    FLOAT,
    sn_ratio        FLOAT,
    bead_mfi_json   NVARCHAR(MAX)
);
GO

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('dbo.QCSampleCache') AND name = '_dedup')
BEGIN
    ALTER TABLE dbo.QCSampleCache ADD
        _dedup AS (catalog_group + '|' + bead_lot + '|'
                   + tray_id + '|'
                   + ISNULL(sample_id_name, '') + '|'
                   + ISNULL(patient_id, '')) PERSISTED;
END;
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'UX_QCSampleCache_Dedup')
    CREATE UNIQUE INDEX UX_QCSampleCache_Dedup ON QCSampleCache(_dedup);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCSampleCache_GroupLot')
    CREATE INDEX IX_QCSampleCache_GroupLot ON QCSampleCache(catalog_group, bead_lot);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QCSampleCache_AnalysisDT')
    CREATE INDEX IX_QCSampleCache_AnalysisDT ON QCSampleCache(analysis_dt);
GO

-- Migration: drop InstrumentRunCache if tray_id has wrong type (INT instead of NVARCHAR).
-- The table is a disposable cache that gets lazily repopulated from the vendor DB.
-- NOTE: must join on user_type_id (not system_type_id) because nvarchar and sysname
-- share the same system_type_id, which caused this migration to fire on every startup.
IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON c.user_type_id = t.user_type_id
    WHERE c.object_id = OBJECT_ID('dbo.InstrumentRunCache')
      AND c.name = 'tray_id'
      AND t.name != 'nvarchar'
)
    DROP TABLE dbo.InstrumentRunCache;
GO

-- Instrument Run Cache: pre-computed per-run instrument metrics from vendor DB
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'InstrumentRunCache')
CREATE TABLE InstrumentRunCache (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    instrument      NVARCHAR(200) NOT NULL,
    tray_id         NVARCHAR(200) NOT NULL,
    analysis_dt     DATETIME2     NOT NULL,
    run_name        NVARCHAR(200),
    sn_median       FLOAT,
    sn_min          FLOAT,
    sn_max          FLOAT,
    nc_median       FLOAT,
    nc_min          FLOAT,
    nc_max          FLOAT,
    count_median    FLOAT,
    count_min       FLOAT,
    count_max       FLOAT
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'UX_InstrumentRunCache_Dedup')
    CREATE UNIQUE INDEX UX_InstrumentRunCache_Dedup ON InstrumentRunCache(instrument, tray_id);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_InstrumentRunCache_AnalysisDT')
    CREATE INDEX IX_InstrumentRunCache_AnalysisDT ON InstrumentRunCache(analysis_dt);
GO

-- Operator Run Cache: pre-computed per-run operator metrics from vendor DB
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'OperatorRunCache')
CREATE TABLE OperatorRunCache (
    id              INT IDENTITY(1,1) PRIMARY KEY,
    operator        NVARCHAR(200) NOT NULL,
    tray_id         NVARCHAR(200) NOT NULL,
    analysis_dt     DATETIME2     NOT NULL,
    run_name        NVARCHAR(200),
    sn_median       FLOAT,
    sn_min          FLOAT,
    sn_max          FLOAT,
    nc_median       FLOAT,
    nc_min          FLOAT,
    nc_max          FLOAT,
    count_median    FLOAT,
    count_min       FLOAT,
    count_max       FLOAT
);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'UX_OperatorRunCache_Dedup')
    CREATE UNIQUE INDEX UX_OperatorRunCache_Dedup ON OperatorRunCache(operator, tray_id);
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_OperatorRunCache_AnalysisDT')
    CREATE INDEX IX_OperatorRunCache_AnalysisDT ON OperatorRunCache(analysis_dt);
GO
