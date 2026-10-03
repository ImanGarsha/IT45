from .arcus import Arcus
from .base import Venue
from .bulk import Bulk
from .hyperliquid import Hyperliquid
from .meridian import Meridian
from .reya import Reya

VENUES: dict[str, type[Venue]] = {
    v.name: v for v in (Hyperliquid, Arcus, Bulk, Reya, Meridian)
}

__all__ = ["VENUES", "Venue"]
