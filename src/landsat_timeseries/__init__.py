"""Multi-year Landsat composites from the Microsoft Planetary Computer STAC API."""

__version__ = "0.1.0"

from .config import DEFAULT_YEARS, RunConfig, YearSpec, load_years  # noqa: E402
from .pipeline import run  # noqa: E402

__all__ = ["DEFAULT_YEARS", "RunConfig", "YearSpec", "load_years", "run", "__version__"]
