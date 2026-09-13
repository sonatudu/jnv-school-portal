"""Flexible class Students table: columns, cell edits, and class roster order."""

from datetime import date, datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils.safestring import mark_safe

from accounts.models import UserCategory

from school.models import (
    AreaType,
    BloodGroup,
    Gender,
    House,
    SocialCategory,
    Student,
    StudentBiodataRow,
    StudentClassMembership,
    StudentHouseMembership,
    StudentTableColumn,
    StudentTableLayout,
)


BUILTIN_COLUMNS = (
    {"key": "roll_number", "label": "Roll", "input": "number"},
    {"key": "admission_number", "label": "Admission No.", "input": "text"},
    {"key": "full_name", "label": "Name", "input": "text"},
    {"key": "father_name", "label": "Father's name", "input": "text"},
    {"key": "mother_name", "label": "Mother's name", "input": "text"},
    {"key": "gender", "label": "Gender", "input": "select"},
    {"key": "date_of_birth", "label": "Date of birth", "input": "date"},
    {"key": "social_category", "label": "Category", "input": "select"},
    {"key": "area_type", "label": "Rural/Urban", "input": "select"},
    {"key": "native_district", "label": "Native district", "input": "text"},
    {"key": "house", "label": "House", "input": "select"},
    {"key": "blood_group", "label": "Blood group", "input": "select"},
)

BUILTIN_BY_KEY = {column["key"]: column for column in BUILTIN_COLUMNS}
BUILTIN_LABELS = {column["label"].casefold() for column in BUILTIN_COLUMNS}
EXTRA_PREFIX = "extra:"

COLUMN_ACTIONS = {"add", "delete"}
TABLE_ACTIONS = {
    "add",
    "add_column",
    "delete",
    "delete_column",
    "hide_column",
    "show_column",
    "move_column",
    "save_cell",
    "move_row",
    "delete_row",
    "restore_row",
    "undo",
    "redo",
}

HISTORY_SESSION_KEY = "jnv_students_table_history"
HISTORY_STACK_CAP = 50


def extra_column_key(column):
    return f"{EXTRA_PREFIX}{column.pk}"


def is_extra_key(key):
    return (key or "").startswith(EXTRA_PREFIX)


def extra_column_id(key):
    try:
        return int((key or "")[len(EXTRA_PREFIX) :])
    except (TypeError, ValueError):
        return None


def _require_admin(request):
    if request.user.category != UserCategory.ADMINISTRATION:
        raise PermissionDenied


def _history_scope(class_section, selected_year):
    year_pk = selected_year.pk if selected_year is not None else "none"
    return f"{class_section.pk}:{year_pk}"


def _history_stacks(request, class_section, selected_year):
    store = request.session.get(HISTORY_SESSION_KEY) or {}
    key = _history_scope(class_section, selected_year)
    stacks = store.get(key) or {"undo": [], "redo": []}
    undo = list(stacks.get("undo") or [])
    redo = list(stacks.get("redo") or [])
    return store, key, {"undo": undo, "redo": redo}


def _write_history(request, store, key, stacks):
    store[key] = stacks
    request.session[HISTORY_SESSION_KEY] = store
    request.session.modified = True


def table_history_flags(request, class_section, selected_year):
    _store, _key, stacks = _history_stacks(request, class_section, selected_year)
    return bool(stacks["undo"]), bool(stacks["redo"])


def _push_history(request, class_section, selected_year, entry):
    store, key, stacks = _history_stacks(request, class_section, selected_year)
    stacks["undo"].append(entry)
    if len(stacks["undo"]) > HISTORY_STACK_CAP:
        stacks["undo"] = stacks["undo"][-HISTORY_STACK_CAP:]
    stacks["redo"] = []
    _write_history(request, store, key, stacks)


def _column_spec_by_key(key):
    columns, _hidden = resolved_columns(
        include_hidden=True,
        houses=House.objects.none(),
    )
    return next((item for item in columns if item["key"] == key), None)


def _apply_recorded_action(request, class_section, selected_year, fields):
    action = (fields.get("table_action") or fields.get("column_action") or "").strip()
    try:
        if action == "save_cell":
            student = Student.objects.filter(pk=fields.get("student_id")).first()
            column = _column_spec_by_key((fields.get("column_key") or "").strip())
            if student is None or column is None:
                return False
            save_student_cell(
                student,
                column,
                fields.get("value"),
                selected_year,
            )
            return True
        if action == "move_row":
            student = Student.objects.filter(pk=fields.get("student_id")).first()
            direction = (fields.get("direction") or "").strip()
            if student is None or direction not in {"up", "down"}:
                return False
            return move_student_row(
                student, class_section, selected_year, direction
            )
        if action == "move_column":
            return _move_column_fields(
                (fields.get("column_key") or "").strip(),
                (fields.get("direction") or "").strip(),
            )
        if action in {"hide_column", "show_column"}:
            key = (fields.get("column_key") or "").strip()
            if not key or key not in BUILTIN_BY_KEY:
                return False
            ensure_table_layout()
            StudentTableLayout.objects.update_or_create(
                column_key=key,
                defaults={"is_hidden": action == "hide_column"},
            )
            return True
        if action == "delete_row":
            student = Student.objects.filter(pk=fields.get("student_id")).first()
            if student is None or selected_year is None:
                return False
            return remove_student_from_class(student, class_section, selected_year)
        if action == "restore_row":
            student = Student.objects.filter(pk=fields.get("student_id")).first()
            if student is None or selected_year is None:
                return False
            StudentClassMembership.objects.create(
                student=student,
                class_section=class_section,
                academic_year=selected_year,
            )
            return True
        if action in {"add", "add_column"}:
            label = (fields.get("label") or "").strip()
            field_type = (
                fields.get("field_type") or StudentTableColumn.FieldType.TEXT
            ).strip()
            if field_type not in StudentTableColumn.FieldType.values:
                field_type = StudentTableColumn.FieldType.TEXT
            if not label or label.casefold() in BUILTIN_LABELS:
                return False
            next_order = (
                StudentTableColumn.objects.aggregate(m=Max("sort_order"))["m"] or 0
            ) + 1
            column = StudentTableColumn.objects.create(
                label=label,
                field_type=field_type,
                sort_order=next_order,
            )
            if StudentTableLayout.objects.exists():
                max_order = (
                    StudentTableLayout.objects.aggregate(m=Max("sort_order"))["m"]
                    or 0
                )
                StudentTableLayout.objects.get_or_create(
                    column_key=extra_column_key(column),
                    defaults={"sort_order": max_order + 1},
                )
            return True
        if action in {"delete", "delete_column"}:
            column = _column_from_fields(fields)
            if column is None:
                return False
            StudentTableLayout.objects.filter(
                column_key=extra_column_key(column)
            ).delete()
            column.delete()
            return True
    except (ValueError, IntegrityError):
        return False
    return False


def _run_history(request, class_section, selected_year, side):
    store, key, stacks = _history_stacks(request, class_section, selected_year)
    if side not in {"undo", "redo"} or not stacks[side]:
        messages.error(
            request,
            "Nothing to undo." if side == "undo" else "Nothing to redo.",
        )
        return True
    entry = stacks[side].pop()
    fields = entry.get(side) or {}
    if not _apply_recorded_action(request, class_section, selected_year, fields):
        stacks[side].append(entry)
        _write_history(request, store, key, stacks)
        messages.error(
            request,
            "Could not undo that change."
            if side == "undo"
            else "Could not redo that change.",
        )
        return True
    other = "redo" if side == "undo" else "undo"
    stacks[other].append(entry)
    _write_history(request, store, key, stacks)
    messages.success(
        request,
        "Undid last change." if side == "undo" else "Redid last change.",
    )
    return True


def _history_entry(undo_fields, redo_fields):
    return {"undo": undo_fields, "redo": redo_fields}


def default_column_keys(extra_columns=None):
    extras = extra_columns
    if extras is None:
        extras = StudentTableColumn.objects.order_by("sort_order", "id")
    keys = [column["key"] for column in BUILTIN_COLUMNS]
    keys.extend(extra_column_key(column) for column in extras)
    return keys


def ensure_table_layout(extra_columns=None):
    extras = list(extra_columns) if extra_columns is not None else list(
        StudentTableColumn.objects.order_by("sort_order", "id")
    )
    default_keys = default_column_keys(extras)
    existing = {
        row.column_key: row for row in StudentTableLayout.objects.all()
    }
    if not existing:
        StudentTableLayout.objects.bulk_create(
            [
                StudentTableLayout(column_key=key, sort_order=index)
                for index, key in enumerate(default_keys)
            ]
        )
        return list(StudentTableLayout.objects.order_by("sort_order", "id"))

    next_order = max((row.sort_order for row in existing.values()), default=-1) + 1
    missing = [
        StudentTableLayout(column_key=key, sort_order=index)
        for index, key in enumerate(default_keys)
        if key not in existing
    ]
    if missing:
        for offset, row in enumerate(missing):
            row.sort_order = next_order + offset
        StudentTableLayout.objects.bulk_create(missing)
        existing = {
            row.column_key: row for row in StudentTableLayout.objects.all()
        }
    return sorted(existing.values(), key=lambda row: (row.sort_order, row.pk))


def _column_spec(key, extra_by_key, houses, hidden=False):
    if is_extra_key(key):
        column = extra_by_key.get(key)
        if column is None:
            return None
        return {
            "key": key,
            "label": column.label,
            "input": "text",
            "editable": True,
            "is_builtin": False,
            "is_hidden": hidden,
            "column_id": column.pk,
            "choices": [],
        }
    builtin = BUILTIN_BY_KEY.get(key)
    if builtin is None:
        return None
    spec = {
        "key": key,
        "label": mark_safe(builtin["label"]),
        "input": builtin["input"],
        "editable": True,
        "is_builtin": True,
        "is_hidden": hidden,
        "column_id": "",
        "choices": [],
    }
    if key == "gender":
        spec["choices"] = list(Gender.choices)
    elif key == "social_category":
        spec["choices"] = [("", "—")] + list(SocialCategory.choices)
    elif key == "area_type":
        spec["choices"] = [("", "—")] + list(AreaType.choices)
    elif key == "blood_group":
        spec["choices"] = [("", "—")] + list(BloodGroup.choices)
    elif key == "house":
        spec["choices"] = [("", "—")] + [(str(house.pk), house.name) for house in houses]
    return spec


def resolved_columns(include_hidden=False, houses=None):
    extras = list(StudentTableColumn.objects.order_by("sort_order", "id"))
    extra_by_key = {extra_column_key(column): column for column in extras}
    houses = list(houses) if houses is not None else []
    layouts = list(StudentTableLayout.objects.order_by("sort_order", "id"))
    if layouts:
        keys = [row.column_key for row in layouts]
        hidden = {row.column_key for row in layouts if row.is_hidden}
        for key in default_column_keys(extras):
            if key not in hidden and key not in keys:
                keys.append(key)
    else:
        keys = default_column_keys(extras)
        hidden = set()

    visible = []
    hidden_columns = []
    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        spec = _column_spec(key, extra_by_key, houses, hidden=key in hidden)
        if spec is None:
            continue
        if spec["is_hidden"]:
            hidden_columns.append(spec)
            if include_hidden:
                visible.append(spec)
            continue
        visible.append(spec)
    return visible, hidden_columns


def _house_for_student(student):
    houses = getattr(student, "year_houses", None)
    if houses:
        return houses[0].house
    return None


def raw_cell_value(student, column):
    key = column["key"]
    if not column["is_builtin"]:
        by_label = {row.label: row.value for row in student.extra_biodata_rows.all()}
        return (by_label.get(column["label"]) or "").strip()
    if key == "full_name":
        return student.full_name
    if key == "date_of_birth":
        return student.date_of_birth.isoformat() if student.date_of_birth else ""
    if key == "house":
        house = _house_for_student(student)
        return str(house.pk) if house else ""
    if key == "roll_number":
        return str(student.roll_number)
    return getattr(student, key, "") or ""


def display_cell_value(student, column):
    key = column["key"]
    if not column["is_builtin"]:
        value = raw_cell_value(student, column)
        return value or "—"
    if key == "gender":
        return student.get_gender_display()
    if key == "social_category":
        return student.get_social_category_display() or "—"
    if key == "area_type":
        return student.get_area_type_display() or "—"
    if key == "blood_group":
        return student.get_blood_group_display() or "—"
    if key == "house":
        house = _house_for_student(student)
        return str(house) if house else "—"
    if key == "date_of_birth":
        return student.date_of_birth
    if key == "full_name":
        return student.full_name
    value = raw_cell_value(student, column)
    return value or "—"


def attach_table_cells(students, columns):
    for student in students:
        student.table_cells = [
            {
                "key": column["key"],
                "input": column["input"],
                "choices": column["choices"],
                "editable": column["editable"],
                "edit_value": raw_cell_value(student, column),
                "display": display_cell_value(student, column),
                "is_name": column["key"] == "full_name",
                "is_admission": column["key"] == "admission_number",
            }
            for column in columns
        ]


def parse_full_name(value):
    parts = [part for part in (value or "").split() if part]
    if not parts:
        raise ValueError("Enter a name.")
    if len(parts) == 1:
        return parts[0], "", parts[0]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _parse_date(value):
    text = (value or "").strip()
    if not text:
        raise ValueError("Enter a date of birth.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%d/%m/%Y").date()
        except ValueError as exc:
            raise ValueError("Enter the date as YYYY-MM-DD.") from exc


def save_student_cell(student, column, value, selected_year):
    key = column["key"]
    value = "" if value is None else str(value)
    if not column["is_builtin"]:
        next_order = (
            student.extra_biodata_rows.aggregate(m=Max("sort_order"))["m"] or 0
        ) + 1
        row, created = StudentBiodataRow.objects.update_or_create(
            student=student,
            label=column["label"],
            defaults={"value": value.strip()},
        )
        if created:
            row.sort_order = next_order
            row.save(update_fields=["sort_order"])
        return

    stripped = value.strip()
    if key == "roll_number":
        if not stripped.isdigit() or int(stripped) < 1:
            raise ValueError("Roll number must be a positive whole number.")
        student.roll_number = int(stripped)
        student.save(update_fields=["roll_number"])
        return
    if key == "admission_number":
        if not stripped:
            raise ValueError("Enter an admission number.")
        student.admission_number = stripped
        student.save(update_fields=["admission_number"])
        return
    if key == "full_name":
        first_name, middle_name, last_name = parse_full_name(stripped)
        student.first_name = first_name
        student.middle_name = middle_name
        student.last_name = last_name
        student.save(update_fields=["first_name", "middle_name", "last_name"])
        return
    if key == "date_of_birth":
        student.date_of_birth = _parse_date(stripped)
        student.save(update_fields=["date_of_birth"])
        return
    if key == "gender":
        if stripped not in Gender.values:
            raise ValueError("Choose a valid gender.")
        student.gender = stripped
        student.save(update_fields=["gender"])
        return
    if key == "social_category":
        if stripped and stripped not in SocialCategory.values:
            raise ValueError("Choose a valid category.")
        student.social_category = stripped
        student.save(update_fields=["social_category"])
        return
    if key == "area_type":
        if stripped and stripped not in AreaType.values:
            raise ValueError("Choose rural or urban.")
        student.area_type = stripped
        student.save(update_fields=["area_type"])
        return
    if key == "blood_group":
        if stripped and stripped not in BloodGroup.values:
            raise ValueError("Choose a valid blood group.")
        student.blood_group = stripped
        student.save(update_fields=["blood_group"])
        return
    if key == "house":
        if selected_year is None:
            raise ValueError("Select an academic year before changing house.")
        if not stripped:
            StudentHouseMembership.objects.filter(
                student=student,
                academic_year=selected_year,
            ).delete()
            return
        house = House.objects.filter(pk=stripped).first()
        if house is None:
            raise ValueError("House not found.")
        StudentHouseMembership.objects.update_or_create(
            student=student,
            academic_year=selected_year,
            defaults={"house": house},
        )
        return
    if key in {"father_name", "mother_name", "native_district"}:
        setattr(student, key, stripped)
        student.save(update_fields=[key])
        return
    raise ValueError("That column cannot be edited.")


def class_roster(class_section, selected_year):
    if selected_year is None:
        return []
    return list(
        Student.objects.filter(
            class_memberships__academic_year=selected_year,
            class_memberships__class_section=class_section,
        ).order_by("roll_number", "first_name", "last_name", "admission_number")
    )


def move_student_row(student, class_section, selected_year, direction):
    roster = class_roster(class_section, selected_year)
    ids = [row.pk for row in roster]
    try:
        index = ids.index(student.pk)
    except ValueError:
        return False
    swap_at = index - 1 if direction == "up" else index + 1
    if swap_at < 0 or swap_at >= len(roster):
        return False
    first, second = roster[index], roster[swap_at]
    if first.roll_number == second.roll_number:
        return False
    unused = (
        max((row.roll_number for row in roster), default=0) + 1000
    )
    old_first, old_second = first.roll_number, second.roll_number
    with transaction.atomic():
        first.roll_number = unused
        first.save(update_fields=["roll_number"])
        second.roll_number = old_first
        second.save(update_fields=["roll_number"])
        first.roll_number = old_second
        first.save(update_fields=["roll_number"])
    return True


def remove_student_from_class(student, class_section, selected_year):
    deleted, _ = StudentClassMembership.objects.filter(
        student_id=student.pk,
        class_section=class_section,
        academic_year=selected_year,
    ).delete()
    return deleted > 0


def _add_column(request):
    label = (request.POST.get("label") or "").strip()
    field_type = (
        request.POST.get("field_type") or StudentTableColumn.FieldType.TEXT
    ).strip()
    if field_type not in StudentTableColumn.FieldType.values:
        field_type = StudentTableColumn.FieldType.TEXT
    if not label:
        messages.error(request, "Enter a name for the extra column.")
        return None
    if label.casefold() in BUILTIN_LABELS:
        messages.error(request, "That name is already a built-in column.")
        return None
    next_order = (
        StudentTableColumn.objects.aggregate(m=Max("sort_order"))["m"] or 0
    ) + 1
    try:
        column = StudentTableColumn.objects.create(
            label=label,
            field_type=field_type,
            sort_order=next_order,
        )
    except IntegrityError:
        messages.error(request, "That column name already exists.")
        return None
    if StudentTableLayout.objects.exists():
        max_order = (
            StudentTableLayout.objects.aggregate(m=Max("sort_order"))["m"] or 0
        )
        StudentTableLayout.objects.get_or_create(
            column_key=extra_column_key(column),
            defaults={"sort_order": max_order + 1},
        )
    messages.success(request, f"Added column “{label}”.")
    return _history_entry(
        {
            "column_action": "delete",
            "column_id": str(column.pk),
            "label": label,
        },
        {
            "column_action": "add",
            "label": label,
            "field_type": field_type,
        },
    )


def _column_from_fields(fields):
    column_id = fields.get("column_id")
    if column_id:
        found = StudentTableColumn.objects.filter(pk=column_id).first()
        if found is not None:
            return found
    key = (fields.get("column_key") or "").strip()
    if is_extra_key(key):
        found = StudentTableColumn.objects.filter(pk=extra_column_id(key)).first()
        if found is not None:
            return found
    label = (fields.get("label") or "").strip()
    if label:
        return StudentTableColumn.objects.filter(label=label).first()
    return None


def _column_from_post(request):
    return _column_from_fields(request.POST)


def _delete_extra_column(request):
    column = _column_from_post(request)
    if column is None:
        messages.error(request, "Column not found.")
        return None
    removed = column.label
    field_type = column.field_type
    StudentTableLayout.objects.filter(column_key=extra_column_key(column)).delete()
    column.delete()
    messages.success(request, f"Removed column “{removed}”.")
    return _history_entry(
        {
            "column_action": "add",
            "label": removed,
            "field_type": field_type,
        },
        {
            "column_action": "delete",
            "label": removed,
        },
    )


def _set_column_hidden(request, hidden):
    key = (request.POST.get("column_key") or "").strip()
    if not key or key not in BUILTIN_BY_KEY:
        messages.error(request, "That built-in column cannot be changed.")
        return None
    ensure_table_layout()
    StudentTableLayout.objects.update_or_create(
        column_key=key,
        defaults={"is_hidden": hidden},
    )
    label = BUILTIN_BY_KEY[key]["label"]
    if hidden:
        messages.success(request, f"Hidden column “{label}”.")
    else:
        messages.success(request, f"Shown column “{label}”.")
    undo_action = "show_column" if hidden else "hide_column"
    redo_action = "hide_column" if hidden else "show_column"
    return _history_entry(
        {"table_action": undo_action, "column_key": key},
        {"table_action": redo_action, "column_key": key},
    )


def _move_column_fields(key, direction):
    if not key or direction not in {"left", "right"}:
        return False
    extras = list(StudentTableColumn.objects.order_by("sort_order", "id"))
    ensure_table_layout(extras)
    layouts = list(
        StudentTableLayout.objects.filter(is_hidden=False).order_by(
            "sort_order", "id"
        )
    )
    known = set(default_column_keys(extras))
    layouts = [row for row in layouts if row.column_key in known]
    keys = [row.column_key for row in layouts]
    try:
        index = keys.index(key)
    except ValueError:
        return False
    swap_at = index - 1 if direction == "left" else index + 1
    if swap_at < 0 or swap_at >= len(layouts):
        return False
    left, right = layouts[index], layouts[swap_at]
    left_order, right_order = left.sort_order, right.sort_order
    if left_order == right_order:
        left_order, right_order = index, swap_at
    left.sort_order, right.sort_order = right_order, left_order
    left.save(update_fields=["sort_order"])
    right.save(update_fields=["sort_order"])
    return True


def _move_column(request):
    key = (request.POST.get("column_key") or "").strip()
    direction = (request.POST.get("direction") or "").strip()
    extras = list(StudentTableColumn.objects.order_by("sort_order", "id"))
    ensure_table_layout(extras)
    visible = {
        row.column_key
        for row in StudentTableLayout.objects.filter(is_hidden=False)
        if row.column_key in set(default_column_keys(extras))
    }
    if key not in visible and key not in BUILTIN_BY_KEY and not is_extra_key(key):
        messages.error(request, "Column not found.")
        return None
    if not _move_column_fields(key, direction):
        return None
    inverse = "right" if direction == "left" else "left"
    return _history_entry(
        {"table_action": "move_column", "column_key": key, "direction": inverse},
        {"table_action": "move_column", "column_key": key, "direction": direction},
    )


def _save_cell(request, class_section, selected_year):
    student = Student.objects.filter(pk=request.POST.get("student_id")).first()
    if student is None:
        messages.error(request, "Student not found.")
        return None
    if selected_year and not student.class_memberships.filter(
        academic_year=selected_year,
        class_section=class_section,
    ).exists():
        messages.error(request, "That student is not in this class.")
        return None
    column_key = (request.POST.get("column_key") or "").strip()
    column = _column_spec_by_key(column_key)
    if column is None:
        messages.error(request, "Column not found.")
        return None
    old_value = raw_cell_value(student, column)
    new_value = request.POST.get("value")
    try:
        save_student_cell(student, column, new_value, selected_year)
    except ValueError as exc:
        messages.error(request, str(exc))
        return None
    except IntegrityError:
        messages.error(request, "That value is already used by another student.")
        return None
    messages.success(request, "Saved.")
    old_text = "" if old_value is None else str(old_value)
    new_text = "" if new_value is None else str(new_value)
    if old_text == new_text:
        return None
    return _history_entry(
        {
            "table_action": "save_cell",
            "student_id": str(student.pk),
            "column_key": column["key"],
            "value": old_text,
        },
        {
            "table_action": "save_cell",
            "student_id": str(student.pk),
            "column_key": column["key"],
            "value": new_text,
        },
    )


def _move_row(request, class_section, selected_year):
    student = Student.objects.filter(pk=request.POST.get("student_id")).first()
    if student is None or selected_year is None:
        messages.error(request, "Student not found.")
        return None
    direction = (request.POST.get("direction") or "").strip()
    if direction not in {"up", "down"}:
        messages.error(request, "Choose up or down.")
        return None
    if not move_student_row(student, class_section, selected_year, direction):
        messages.error(request, "That row cannot be moved further.")
        return None
    messages.success(request, "Updated roll numbers.")
    inverse = "down" if direction == "up" else "up"
    return _history_entry(
        {
            "table_action": "move_row",
            "student_id": str(student.pk),
            "direction": inverse,
        },
        {
            "table_action": "move_row",
            "student_id": str(student.pk),
            "direction": direction,
        },
    )


def _delete_row(request, class_section, selected_year):
    student = Student.objects.filter(pk=request.POST.get("student_id")).first()
    if student is None or selected_year is None:
        messages.error(request, "Student not found.")
        return None
    if not remove_student_from_class(student, class_section, selected_year):
        messages.error(request, "That student is not in this class.")
        return None
    messages.success(
        request,
        f"Removed {student.full_name} from the class. The student record was kept.",
    )
    return _history_entry(
        {"table_action": "restore_row", "student_id": str(student.pk)},
        {"table_action": "delete_row", "student_id": str(student.pk)},
    )


def _restore_row(request, class_section, selected_year):
    student = Student.objects.filter(pk=request.POST.get("student_id")).first()
    if student is None or selected_year is None:
        messages.error(request, "Student not found.")
        return None
    if StudentClassMembership.objects.filter(
        student=student,
        class_section=class_section,
        academic_year=selected_year,
    ).exists():
        messages.error(request, "That student is already in this class.")
        return None
    try:
        StudentClassMembership.objects.create(
            student=student,
            class_section=class_section,
            academic_year=selected_year,
        )
    except IntegrityError:
        messages.error(request, "That student is already in a class this year.")
        return None
    messages.success(request, f"Restored {student.full_name} to the class.")
    return _history_entry(
        {"table_action": "delete_row", "student_id": str(student.pk)},
        {"table_action": "restore_row", "student_id": str(student.pk)},
    )


def handle_student_table_post(request, class_section=None, selected_year=None):
    """Handle Students table editor POSTs. Return True if handled."""
    if request.method != "POST":
        return False
    action = (
        request.POST.get("table_action") or request.POST.get("column_action") or ""
    ).strip()
    if action not in TABLE_ACTIONS:
        return False
    _require_admin(request)
    if action in {"undo", "redo"}:
        return _run_history(request, class_section, selected_year, action)
    history = None
    if action in {"add", "add_column"}:
        history = _add_column(request)
    elif action in {"delete", "delete_column"}:
        history = _delete_extra_column(request)
    elif action == "hide_column":
        history = _set_column_hidden(request, True)
    elif action == "show_column":
        history = _set_column_hidden(request, False)
    elif action == "move_column":
        history = _move_column(request)
    elif action == "save_cell":
        history = _save_cell(request, class_section, selected_year)
    elif action == "move_row":
        history = _move_row(request, class_section, selected_year)
    elif action == "delete_row":
        history = _delete_row(request, class_section, selected_year)
    elif action == "restore_row":
        history = _restore_row(request, class_section, selected_year)
    else:
        return False
    if history:
        _push_history(request, class_section, selected_year, history)
    return True


def handle_student_table_column_post(request, class_section=None, selected_year=None):
    """Back-compatible alias used by the class Students admin view."""
    return handle_student_table_post(request, class_section, selected_year)
