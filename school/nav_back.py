from django.urls import NoReverseMatch, reverse

LIFE_PAGES = frozenset(
    {
        "portal-events",
        "portal-house-life",
        "portal-library",
        "portal-exams",
        "portal-vmc",
        "portal-sick-bay",
        "portal-visitors",
        "portal-migration",
        "portal-vvn",
    }
)

# Top-level nav landings: nothing further up besides leaving the site.
PORTAL_HOMES = frozenset(
    {
        "portal-home",
        "portal-dashboard",
        "portal-staff",
        "portal-parent",
        "portal-life",
        "portal-mess",
        "portal-circulars",
        "portal-exceptions",
        "portal-outings",
    }
)


def _link(url_name, label, args=None):
    try:
        return {"href": reverse(url_name, args=args or []), "label": label}
    except NoReverseMatch:
        return None


def resolve_nav_back(request):
    """Structural parent of this page, or None on a landing. Never uses Referer."""
    return _mapped_parent(request)


def _mapped_parent(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None

    name = match.url_name or ""
    namespaces = set(match.namespaces or [])

    if name in PORTAL_HOMES:
        return None
    if name == "login" and "admin" not in namespaces:
        return _link("portal-home", "Home")
    if name in LIFE_PAGES:
        return _link("portal-life", "Vidyalaya life")
    if name == "portal-staff-mark":
        return _link("portal-staff", "Today")
    if name == "portal-parent-history":
        return _link("portal-parent", "My ward")

    if "admin" in namespaces:
        return _admin_back(request, name)

    return None


def _admin_back(request, name):
    if name in ("index",) or name.endswith("_changelist"):
        return None
    if name in ("login", "logout"):
        return _link("portal-home", "Home")
    if name in ("password_change", "password_change_done"):
        return _link("admin:index", "Admin")

    if name.startswith("school_classsection_class") or name == "school_classsection_attendance_report":
        return _link("admin:school_classsection_changelist", "Classes")
    if name.startswith("school_house_house") or name == "school_house_attendance_report":
        return _link("admin:school_house_changelist", "Houses")
    if name == "school_student_biodata":
        from_class = request.GET.get("from_class")
        from_house = request.GET.get("from_house")
        if from_class:
            return _link("admin:school_classsection_class_students", "Class", args=[from_class])
        if from_house:
            return _link("admin:school_house_house_students", "House", args=[from_house])
        return _link("admin:school_student_changelist", "Students")
    if name == "school_student_attendance_history":
        return _link("admin:school_student_changelist", "Students")
    if name == "accounts_user_profile":
        return _link("admin:accounts_user_changelist", "Users")

    for suffix in ("_change", "_add", "_delete", "_history"):
        if name.endswith(suffix):
            changelist = f"admin:{name[: -len(suffix)]}_changelist"
            link = _link(changelist, "List")
            if link:
                return link
            break

    if name.startswith("school_activitysession_"):
        return _link("admin:school_activitysession_changelist", "Sessions")

    return _link("admin:index", "Admin")
