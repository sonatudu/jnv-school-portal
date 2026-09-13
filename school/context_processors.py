from django.db.utils import OperationalError, ProgrammingError

from .models import VidyalayaProfile
from .nav_back import resolve_nav_back


def vidyalaya(request):
    try:
        return {"vidyalaya": VidyalayaProfile.load()}
    except (OperationalError, ProgrammingError):
        return {"vidyalaya": None}


def nav_back(request):
    return {"nav_back": resolve_nav_back(request)}
