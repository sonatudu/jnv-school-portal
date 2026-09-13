"""Add, update, and remove extra biodata rows from Admin profile pages."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db.models import Max

from accounts.models import UserCategory


def handle_extra_biodata_post(request, queryset, create_kwargs):
    """Handle add/save/delete POST. Return True when the request was a biodata action."""
    if request.method != "POST":
        return False
    action = (request.POST.get("biodata_action") or "").strip()
    if action not in {"add", "save", "delete"}:
        return False
    if request.user.category != UserCategory.ADMINISTRATION:
        raise PermissionDenied
    label = (request.POST.get("label") or "").strip()
    value = (request.POST.get("value") or "").strip()
    if action == "add":
        if not label:
            messages.error(request, "Enter a label for the extra biodata row.")
            return True
        next_order = (queryset.aggregate(m=Max("sort_order"))["m"] or 0) + 1
        try:
            queryset.model.objects.create(
                **create_kwargs,
                label=label,
                value=value,
                sort_order=next_order,
            )
        except IntegrityError:
            messages.error(request, "That biodata label already exists for this record.")
            return True
        messages.success(request, f"Added biodata row “{label}”.")
        return True

    row = queryset.filter(pk=request.POST.get("row_id")).first()
    if row is None:
        messages.error(request, "Biodata row not found.")
        return True
    if action == "delete":
        removed = row.label
        row.delete()
        messages.success(request, f"Removed biodata row “{removed}”.")
        return True
    if not label:
        messages.error(request, "Enter a label for the extra biodata row.")
        return True
    row.label = label
    row.value = value
    try:
        row.save()
    except IntegrityError:
        messages.error(request, "That biodata label already exists for this record.")
        return True
    messages.success(request, f"Saved biodata row “{label}”.")
    return True


def handle_student_table_column_post(request, class_section=None, selected_year=None):
    """Handle Students table editor POSTs. Return True if handled."""
    from school.student_table import handle_student_table_post

    return handle_student_table_post(request, class_section, selected_year)


def extra_column_cells(student, columns):
    """Return display values for each extra table column, from biodata rows."""
    if not columns:
        return []
    by_label = {row.label: row.value for row in student.extra_biodata_rows.all()}
    cells = []
    for column in columns:
        value = (by_label.get(column.label) or "").strip()
        cells.append(value or "—")
    return cells
