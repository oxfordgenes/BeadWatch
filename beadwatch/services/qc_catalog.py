from typing import Dict, Iterable, Optional


CATALOG_GROUP_PREFIXES = ("LSM12", "LSMUTR", "LS1PRA", "LS2PRA", "LS1A04", "LS2A01")


def lot_like_pattern(catalog_group: str, bead_lot: str) -> str:
    """Build a SQL LIKE pattern that targets a specific bead lot."""
    return f"{catalog_group}%[_]{bead_lot}[_]%"


def extract_catalog_group(catalog_id: Optional[str]) -> Optional[str]:
    """Extract normalized catalog group prefix from a CatalogID."""
    if not catalog_id:
        return None
    base = str(catalog_id).split(".")[0].upper()
    for prefix in CATALOG_GROUP_PREFIXES:
        if base.startswith(prefix):
            return prefix
    return None


def extract_bead_lot(catalog_id: Optional[str]) -> Optional[str]:
    """Extract bead lot token from a CatalogID."""
    if not catalog_id:
        return None
    catalog_base = str(catalog_id).split(".")[0]
    parts = catalog_base.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    return None


def select_controls(catalog_id: Optional[str], nc1, nc2, pc1, pc2) -> Dict[str, Optional[float]]:
    """Select NC/PC pair according to LS1/LS2 catalog family preference."""
    if not catalog_id:
        return {"nc": nc1 if nc1 is not None else nc2, "pc": pc1 if pc1 is not None else pc2}
    base = str(catalog_id).split(".")[0].upper()
    if base.startswith("LS2"):
        return {"nc": nc2 if nc2 is not None else nc1, "pc": pc2 if pc2 is not None else pc1}
    return {"nc": nc1 if nc1 is not None else nc2, "pc": pc1 if pc1 is not None else pc2}


def uppercased_value_set(rows: Iterable[dict], key: str) -> set[str]:
    """Return an upper-cased set of values for a given row key."""
    return {str(row[key]).upper() for row in rows}


def uppercased_alias_map(rows: Iterable[dict], key: str, value_key: str) -> Dict[str, str]:
    """Return upper-cased key to display-value mapping from row objects."""
    return {str(row[key]).upper(): row[value_key] for row in rows}
