from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError, transaction
from django.db.models import CharField, Prefetch, Q, Value
from django.db.models.functions import Concat
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AcademicYear,
    ActivitySession,
    ActivitySessionParticipant,
    ActivityType,
    AttendanceEntry,
    AttendanceRevision,
    AudienceKind,
    ClassSection,
    ClassTeacherAssignment,
    ClassGradeOptions,
    ClassGradeStaffAssignment,
    ClassStaffRole,
    ClassTimetableEntry,
    DutyType,
    House,
    HouseMasterAssignment,
    HouseStaffRole,
    ParentProfile,
    Routine,
    RoutineSlot,
    SchoolCalendarDay,
    StaffDutyAssignment,
    Student,
    StudentTableColumn,
    StudentGroup,
    StudentGroupMembership,
    StudentClassMembership,
    StudentGuardian,
    StudentHouseMembership,
    Subject,
    TeacherProfile,
    TeachingAssignment,
    AttendanceStatus,
    Circular,
    MessMenu,
    OutingPass,
    VidyalayaProfile,
    StudentOffice,
    HouseCompetition,
    HouseCompetitionResult,
    SickBayVisit,
    VisitorPass,
    LibraryBook,
    LibraryIssue,
    ExamTerm,
    AssessmentMark,
    CommitteeSeat,
    VidyalayaEvent,
    VvnEntry,
    MigrationRecord,
)
from accounts.models import User, UserCategory


def _apply_day_attendance(request, sessions, students):
    from .attendance_auth import can_change_attendance_status, can_take_attendance

    valid_statuses = {choice[0] for choice in AttendanceStatus.choices}
    reason = (request.POST.get("change_reason") or "")[:200]
    student_ids = {student.pk for student in students}
    entries = {
        (entry.activity_session_id, entry.student_id): entry
        for entry in AttendanceEntry.objects.filter(
            activity_session__in=sessions,
            student_id__in=student_ids,
        )
    }
    to_save = []
    errors = []
    now = timezone.now()
    for session in sessions:
        for student in students:
            posted = (
                request.POST.get(f"status_{session.pk}_{student.pk}") or ""
            ).strip()
            entry = entries.get((session.pk, student.pk))
            if not posted:
                continue
            if posted not in valid_statuses:
                errors.append(f"{student}: invalid status.")
                continue
            if entry is None:
                if not can_take_attendance(request.user, session):
                    continue
                to_save.append(
                    AttendanceEntry(
                        activity_session=session,
                        student=student,
                        status=posted,
                        taken_by=request.user,
                        taken_at=now,
                    )
                )
                continue
            if posted == entry.status:
                continue
            if not can_change_attendance_status(request.user, session):
                continue
            entry.status = posted
            entry.updated_by = request.user
            entry._status_change_reason = reason
            to_save.append(entry)
    if errors:
        return errors
    try:
        with transaction.atomic():
            for obj in to_save:
                obj.save()
    except IntegrityError:
        return [
            "Another user saved attendance at the same time. "
            "Please review the current marks and try again."
        ]
    except ValidationError as exc:
        if hasattr(exc, "messages"):
            return list(exc.messages)
        return [str(exc)]
    return []


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "start_date",
        "end_date",
        "is_current",
        "school_attendance_report_link",
        "attendance_coverage_link",
    )
    list_filter = ("is_current",)
    search_fields = ("name",)
    ordering = ("-start_date",)

    @admin.display(description="Attendance")
    def school_attendance_report_link(self, obj):
        url = reverse("admin:school_activitysession_school_attendance_report")
        return format_html(
            '<a href="{}?academic_year={}">School attendance report</a>',
            url,
            obj.pk,
        )

    @admin.display(description="Coverage")
    def attendance_coverage_link(self, obj):
        url = reverse("admin:school_activitysession_attendance_coverage")
        return format_html(
            '<a href="{}?academic_year={}">Attendance coverage</a>',
            url,
            obj.pk,
        )


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "attendance_report_link",
    )
    list_filter = ()
    search_fields = ("display_name", "grade_name", "section_name")
    ordering = ("grade_number", "section_name", "display_name")
    change_list_template = "admin/school/classsection/change_list.html"
    list_per_page = 200

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["class_list"] = ClassSection.objects.order_by(
            "grade_number",
            "section_name",
            "display_name",
        )
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
            path(
                "class/<int:object_id>/",
                self.admin_site.admin_view(self.class_overview_view),
                name="school_classsection_class_overview",
            ),
            path(
                "class/<int:object_id>/students/",
                self.admin_site.admin_view(self.class_students_view),
                name="school_classsection_class_students",
            ),
            path(
                "class/<int:object_id>/attendance/",
                self.admin_site.admin_view(self.class_attendance_view),
                name="school_classsection_class_attendance",
            ),
            path(
                "class/<int:object_id>/routine/",
                self.admin_site.admin_view(self.class_routine_view),
                name="school_classsection_class_routine",
            ),
            path(
                "<int:object_id>/attendance-report/",
                self.admin_site.admin_view(self.attendance_report_view),
                name="school_classsection_attendance_report",
            ),
        ]
        return extra + super().get_urls()

    @admin.display(description="Attendance")
    def attendance_report_link(self, obj):
        url = reverse("admin:school_classsection_attendance_report", args=[obj.pk])
        return format_html('<a href="{}">Attendance report</a>', url)

    def class_overview_view(self, request, object_id):
        class_section = ClassSection.objects.filter(pk=object_id).first()
        if class_section is None:
            raise Http404("Class not found.")

        years = list(AcademicYear.objects.order_by("-start_date"))
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None
        raw_year = request.GET.get("academic_year") or request.POST.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                selected_year = None

        can_edit = (
            request.user.is_authenticated
            and request.user.is_active
            and request.user.category == UserCategory.ADMINISTRATION
        )
        options = None
        if selected_year:
            options = ClassGradeOptions.objects.filter(
                academic_year=selected_year,
                class_section=class_section,
            ).first()

        if request.method == "POST":
            if not can_edit:
                raise PermissionDenied
            if selected_year is None:
                raise Http404("Academic year not found.")
            class_teacher_id = (request.POST.get("class_teacher") or "").strip()
            assistant_id = (request.POST.get("assistant_class_teacher") or "").strip()
            if assistant_id and assistant_id == class_teacher_id:
                messages.error(
                    request,
                    "Class teacher and assistant class teacher must be different people.",
                )
                assistant_id = ""
            ClassGradeOptions.objects.update_or_create(
                academic_year=selected_year,
                class_section=class_section,
                defaults={"show_assistant_class_teacher": True},
            )
            self._save_class_staff(
                selected_year,
                class_section,
                ClassStaffRole.CLASS_TEACHER,
                class_teacher_id,
            )
            self._save_class_staff(
                selected_year,
                class_section,
                ClassStaffRole.ASSISTANT_CLASS_TEACHER,
                assistant_id,
            )
            return HttpResponseRedirect(
                reverse(
                    "admin:school_classsection_class_overview",
                    args=[class_section.pk],
                )
                + f"?academic_year={selected_year.pk}"
            )

        assignments = {}
        if selected_year:
            assignments = {
                item.role: item
                for item in ClassGradeStaffAssignment.objects.filter(
                    academic_year=selected_year,
                    class_section=class_section,
                ).select_related("teacher__user")
            }
        teachers = TeacherProfile.objects.select_related("user").order_by(
            "user__last_name",
            "user__first_name",
            "user__username",
        )
        show_assistant = True
        context = {
            **self.admin_site.each_context(request),
            "title": str(class_section),
            "opts": self.model._meta,
            "class_section": class_section,
            "years": years,
            "selected_year": selected_year,
            "teachers": teachers,
            "class_teacher": assignments.get(ClassStaffRole.CLASS_TEACHER),
            "assistant_class_teacher": assignments.get(
                ClassStaffRole.ASSISTANT_CLASS_TEACHER
            ),
            "show_assistant_class_teacher": show_assistant,
            "can_edit": can_edit,
            "changelist_url": reverse("admin:school_classsection_changelist"),
            "current_class_section": "home",
            **self._class_section_urls(class_section, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/classsection/class_overview.html",
            context,
        )

    def _class_section_urls(self, class_section, selected_year):
        query = f"?academic_year={selected_year.pk}" if selected_year else ""
        return {
            "overview_url": reverse(
                "admin:school_classsection_class_overview",
                args=[class_section.pk],
            )
            + query,
            "students_url": reverse(
                "admin:school_classsection_class_students",
                args=[class_section.pk],
            )
            + query,
            "attendance_url": reverse(
                "admin:school_classsection_class_attendance",
                args=[class_section.pk],
            )
            + query,
            "routine_url": reverse(
                "admin:school_classsection_class_routine",
                args=[class_section.pk],
            )
            + query,
        }

    def _class_page_year(self, request):
        years = list(AcademicYear.objects.order_by("-start_date"))
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None
        raw_year = request.GET.get("academic_year") or request.POST.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                selected_year = None
        return years, selected_year

    def _class_students(self, class_section, selected_year, query=""):
        if selected_year is None:
            return Student.objects.none()
        students = (
            Student.objects.filter(
                class_memberships__academic_year=selected_year,
                class_memberships__class_section=class_section,
            )
            .select_related("class_section")
            .prefetch_related(
                Prefetch(
                    "house_memberships",
                    queryset=StudentHouseMembership.objects.filter(
                        academic_year=selected_year
                    ).select_related("house"),
                    to_attr="year_houses",
                )
            )
            .order_by("roll_number", "first_name", "last_name", "admission_number")
        )
        query = (query or "").strip()
        if not query:
            return students
        return students.annotate(
            _given_name=Concat(
                "first_name",
                Value(" "),
                "last_name",
                output_field=CharField(),
            )
        ).filter(
            Q(first_name__icontains=query)
            | Q(middle_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(_given_name__icontains=query)
            | Q(admission_number__icontains=query)
            | Q(roll_number__icontains=query)
        )

    def class_students_view(self, request, object_id):
        class_section = ClassSection.objects.filter(pk=object_id).first()
        if class_section is None:
            raise Http404("Class not found.")
        years, selected_year = self._class_page_year(request)
        search_query = (request.GET.get("q") or "").strip()
        can_edit = (
            request.user.is_authenticated
            and request.user.is_active
            and request.user.category == UserCategory.ADMINISTRATION
        )
        from school.student_table import (
            attach_table_cells,
            class_roster,
            handle_student_table_post,
            resolved_columns,
            table_history_flags,
        )

        if handle_student_table_post(request, class_section, selected_year):
            query = {"edit": "1"}
            if selected_year:
                query["academic_year"] = selected_year.pk
            if search_query:
                query["q"] = search_query
            return HttpResponseRedirect(
                reverse(
                    "admin:school_classsection_class_students",
                    args=[class_section.pk],
                )
                + f"?{urlencode(query)}"
            )

        table_edit = can_edit and (
            request.GET.get("edit") == "1" or request.GET.get("edit_columns") == "1"
        )
        houses = list(House.objects.filter(is_active=True).order_by("name"))
        table_columns, hidden_columns = resolved_columns(
            include_hidden=False,
            houses=houses,
        )
        extra_columns = list(StudentTableColumn.objects.order_by("sort_order", "id"))
        students = self._class_students(class_section, selected_year, search_query)
        students = students.prefetch_related("extra_biodata_rows")
        students = list(students)
        attach_table_cells(students, table_columns)
        roster = class_roster(class_section, selected_year)
        roster_index = {row.pk: index for index, row in enumerate(roster)}
        roster_last = len(roster) - 1
        for student in students:
            index = roster_index.get(student.pk)
            student.can_move_up = index is not None and index > 0
            student.can_move_down = index is not None and index < roster_last
        query = {}
        if selected_year:
            query["academic_year"] = selected_year.pk
        if search_query:
            query["q"] = search_query
        view_query = urlencode(query)
        edit_query = urlencode({**query, "edit": "1"})
        can_undo, can_redo = table_history_flags(
            request, class_section, selected_year
        )
        context = {
            **self.admin_site.each_context(request),
            "title": str(class_section),
            "opts": self.model._meta,
            "class_section": class_section,
            "years": years,
            "selected_year": selected_year,
            "search_query": search_query,
            "students": students,
            "extra_columns": extra_columns,
            "table_columns": table_columns,
            "hidden_columns": hidden_columns,
            "can_edit": can_edit,
            "table_edit": table_edit,
            "can_undo": can_undo,
            "can_redo": can_redo,
            "columns_editor_open": table_edit,
            "students_view_query": view_query,
            "students_edit_query": edit_query,
            "changelist_url": reverse("admin:school_classsection_changelist"),
            "current_class_section": "students",
            **self._class_section_urls(class_section, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/classsection/class_students.html",
            context,
        )

    def class_routine_view(self, request, object_id):
        class_section = ClassSection.objects.filter(pk=object_id).first()
        if class_section is None:
            raise Http404("Class not found.")
        years, selected_year = self._class_page_year(request)
        entries = []
        if selected_year:
            entries = list(
                ClassTimetableEntry.objects.filter(
                    academic_year=selected_year,
                    class_section=class_section,
                )
                .select_related("routine_slot", "subject", "teacher__user")
                .order_by("routine_slot__sort_order", "routine_slot__start_time")
            )
        context = {
            **self.admin_site.each_context(request),
            "title": str(class_section),
            "opts": self.model._meta,
            "class_section": class_section,
            "years": years,
            "selected_year": selected_year,
            "timetable_entries": entries,
            "changelist_url": reverse("admin:school_classsection_changelist"),
            "current_class_section": "routine",
            **self._class_section_urls(class_section, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/classsection/class_routine.html",
            context,
        )

    def class_attendance_view(self, request, object_id):
        from .attendance_auth import can_change_attendance_status, can_take_attendance

        class_section = ClassSection.objects.filter(pk=object_id).first()
        if class_section is None:
            raise Http404("Class not found.")
        years, selected_year = self._class_page_year(request)
        selected_date = timezone.localdate()
        raw_date = request.GET.get("date") or request.POST.get("date")
        if raw_date:
            try:
                selected_date = date.fromisoformat(raw_date)
            except ValueError:
                selected_date = timezone.localdate()

        students = list(self._class_students(class_section, selected_year))
        sessions = []
        if selected_year:
            sessions = list(
                ActivitySession.objects.filter(
                    date=selected_date,
                    academic_year=selected_year,
                    audience_kind=AudienceKind.CLASS,
                    class_section=class_section,
                    activity_type__takes_attendance=True,
                )
                .select_related("activity_type", "responsible_staff", "routine_slot")
                .order_by("start_time", "name")
            )

        if request.method == "POST":
            user = request.user
            if (
                not user.is_authenticated
                or not user.is_active
                or user.category == UserCategory.PARENT
            ):
                raise PermissionDenied
            errors = _apply_day_attendance(
                request,
                sessions,
                students,
            )
            if not errors:
                messages.success(request, "Attendance saved.")
                query = urlencode(
                    {
                        "academic_year": selected_year.pk if selected_year else "",
                        "date": selected_date.isoformat(),
                    }
                )
                return HttpResponseRedirect(
                    reverse(
                        "admin:school_classsection_class_attendance",
                        args=[class_section.pk],
                    )
                    + f"?{query}"
                )
            messages.error(request, " ".join(errors))

        entries = {
            (entry.activity_session_id, entry.student_id): entry
            for entry in AttendanceEntry.objects.filter(
                activity_session__in=sessions,
                student__in=students,
            ).select_related("taken_by", "updated_by")
        }
        session_editable = {
            session.pk: can_take_attendance(request.user, session)
            or can_change_attendance_status(request.user, session)
            for session in sessions
        }
        rows = []
        for student in students:
            cells = []
            for session in sessions:
                entry = entries.get((session.pk, student.pk))
                posted = (
                    request.POST.get(f"status_{session.pk}_{student.pk}")
                    if request.method == "POST"
                    else None
                )
                cells.append(
                    {
                        "session": session,
                        "entry": entry,
                        "status": posted
                        if posted is not None
                        else (entry.status if entry else ""),
                        "editable": session_editable.get(session.pk, False),
                    }
                )
            rows.append({"student": student, "cells": cells})

        can_save = any(session_editable.values())
        context = {
            **self.admin_site.each_context(request),
            "title": str(class_section),
            "opts": self.model._meta,
            "class_section": class_section,
            "years": years,
            "selected_year": selected_year,
            "selected_date": selected_date,
            "sessions": sessions,
            "rows": rows,
            "status_choices": AttendanceStatus.choices,
            "can_save": can_save,
            "changelist_url": reverse("admin:school_classsection_changelist"),
            "current_class_section": "attendance",
            **self._class_section_urls(class_section, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/classsection/class_attendance.html",
            context,
        )

    def _apply_class_day_attendance(self, request, sessions, students):
        return _apply_day_attendance(request, sessions, students)

    def _save_class_staff(self, year, class_section, role, teacher_id):
        teacher_id = (teacher_id or "").strip()
        if not teacher_id:
            ClassGradeStaffAssignment.objects.filter(
                academic_year=year,
                class_section=class_section,
                role=role,
            ).delete()
            return
        teacher = TeacherProfile.objects.filter(pk=int(teacher_id)).first()
        if teacher is None:
            return
        ClassGradeStaffAssignment.objects.update_or_create(
            academic_year=year,
            class_section=class_section,
            role=role,
            defaults={"teacher": teacher},
        )

    def _forbidden_report(self, request, message):
        context = {
            **self.admin_site.each_context(request),
            "title": "Class attendance report",
            "message": message,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/mark_attendance_denied.html",
            context,
            status=403,
        )

    def _format_present_rate(self, percentage):
        if percentage is None:
            return "N/A"
        return f"{percentage:.1f}".rstrip("0").rstrip(".") + "%"

    def attendance_report_view(self, request, object_id):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import build_class_attendance_report

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_report(
                request,
                "You are not authorized to view the class attendance report.",
            )

        class_section = ClassSection.objects.filter(pk=object_id).first()
        if class_section is None:
            raise Http404("Class not found.")

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None

        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid start date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid end date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        report = build_class_attendance_report(class_section, selected_year, [])
        if selected_year and date_from and date_to:
            sessions = (
                sessions_user_may_mark(user)
                .filter(
                    audience_kind=AudienceKind.CLASS,
                    class_section=class_section,
                    academic_year=selected_year,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .select_related("activity_type", "class_section")
                .order_by("date", "start_time", "name")
            )
            report = build_class_attendance_report(
                class_section,
                selected_year,
                sessions,
            )

        for row in report["student_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])
            row["history_url"] = reverse(
                "admin:school_student_attendance_history",
                args=[row["student"].pk],
            )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Attendance report: {class_section}",
            "opts": self.model._meta,
            "class_section": class_section,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "report": report,
            "percentage_display": self._format_present_rate(report["percentage"]),
        }
        return TemplateResponse(
            request,
            "admin/school/classsection/attendance_report.html",
            context,
        )


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


class StudentClassMembershipInline(admin.TabularInline):
    model = StudentClassMembership
    extra = 0
    autocomplete_fields = ("class_section", "academic_year")
    verbose_name = "class placement"
    verbose_name_plural = "class placement history"


class StudentGuardianFromStudentInline(admin.TabularInline):
    model = StudentGuardian
    extra = 0
    autocomplete_fields = ("parent_profile",)
    verbose_name = "guardian"
    verbose_name_plural = "guardians"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "admission_number",
        "roll_number",
        "full_name",
        "class_section",
        "gender",
        "date_of_birth",
        "academic_year",
        "is_active",
        "attendance_history_link",
    )
    list_filter = (
        "is_active",
        "academic_year",
        "class_section",
        "gender",
        "social_category",
        "area_type",
    )
    search_fields = (
        "admission_number",
        "first_name",
        "middle_name",
        "last_name",
        "roll_number",
        "father_name",
        "mother_name",
        "native_district",
    )
    list_select_related = ("class_section", "academic_year")
    fieldsets = (
        (
            "Class and roll",
            {
                "fields": (
                    "class_section",
                    "roll_number",
                    "academic_year",
                )
            },
        ),
        (
            None,
            {
                "fields": (
                    "admission_number",
                    "first_name",
                    "middle_name",
                    "last_name",
                    "is_active",
                )
            },
        ),
        (
            "JNV basic details",
            {
                "fields": (
                    "gender",
                    "date_of_birth",
                    "father_name",
                    "mother_name",
                    "social_category",
                    "area_type",
                    "native_district",
                    "blood_group",
                )
            },
        ),
    )
    ordering = (
        "class_section__grade_number",
        "class_section__section_name",
        "roll_number",
        "last_name",
        "first_name",
    )
    inlines = (StudentClassMembershipInline, StudentGuardianFromStudentInline)
    list_per_page = 50

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "roll_number":
            kwargs["label"] = "Roll no."
        elif db_field.name == "class_section":
            kwargs["label"] = "Class"
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        current = AcademicYear.objects.filter(is_current=True).first()
        if current and "academic_year" not in initial:
            initial["academic_year"] = current.pk
        return initial

    def changelist_view(self, request, extra_context=None):
        self._request = request
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
            path(
                "<int:object_id>/biodata/",
                self.admin_site.admin_view(self.student_biodata_view),
                name="school_student_biodata",
            ),
            path(
                "<int:object_id>/attendance-history/",
                self.admin_site.admin_view(self.attendance_history_view),
                name="school_student_attendance_history",
            ),
        ]
        return extra + super().get_urls()

    @admin.display(description="Attendance")
    def attendance_history_link(self, obj):
        url = reverse("admin:school_student_attendance_history", args=[obj.pk])
        return format_html('<a href="{}">Attendance history</a>', url)

    def student_biodata_view(self, request, object_id):
        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_history(
                request,
                "You are not authorized to view student biodata.",
            )

        student = (
            Student.objects.select_related("class_section", "academic_year")
            .filter(pk=object_id)
            .first()
        )
        if student is None:
            raise Http404("Student not found.")

        extra_rows = student.extra_biodata_rows.all()
        from school.biodata_extra import handle_extra_biodata_post

        if handle_extra_biodata_post(
            request,
            extra_rows,
            {"student": student},
        ):
            return HttpResponseRedirect(request.get_full_path())

        houses = (
            StudentHouseMembership.objects.filter(student=student)
            .select_related("house", "academic_year")
            .order_by("-academic_year")
        )
        class_history = (
            StudentClassMembership.objects.filter(student=student)
            .select_related("class_section", "academic_year")
            .order_by("-academic_year")
        )
        guardians = (
            StudentGuardian.objects.filter(student=student)
            .select_related("parent_profile__user")
            .order_by("parent_profile__user__last_name", "parent_profile__user__username")
        )
        from_class = request.GET.get("from_class")
        from_house = request.GET.get("from_house")
        back_url = reverse("admin:school_student_changelist")
        if from_class:
            back_url = reverse(
                "admin:school_classsection_class_students",
                args=[from_class],
            )
        elif from_house:
            back_url = reverse(
                "admin:school_house_house_students",
                args=[from_house],
            )
        context = {
            **self.admin_site.each_context(request),
            "title": f"Biodata: {student.full_name}",
            "opts": self.model._meta,
            "student": student,
            "houses": houses,
            "class_history": class_history,
            "guardians": guardians,
            "extra_rows": extra_rows,
            "back_url": back_url,
            "attendance_url": reverse(
                "admin:school_student_attendance_history",
                args=[student.pk],
            ),
            "change_url": reverse("admin:school_student_change", args=[student.pk]),
            "can_edit": user.category == UserCategory.ADMINISTRATION,
        }
        return TemplateResponse(
            request,
            "admin/school/student/biodata.html",
            context,
        )

    def _forbidden_history(self, request, message):
        context = {
            **self.admin_site.each_context(request),
            "title": "Attendance history",
            "message": message,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/mark_attendance_denied.html",
            context,
            status=403,
        )

    def attendance_history_view(self, request, object_id):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import build_student_attendance_history

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_history(
                request,
                "You are not authorized to view student attendance history.",
            )

        student = (
            Student.objects.select_related("class_section", "academic_year")
            .filter(pk=object_id)
            .first()
        )
        if student is None:
            raise Http404("Student not found.")

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = student.academic_year
        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid start date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid end date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        history = build_student_attendance_history(student, [])
        if selected_year and date_from and date_to:
            sessions = (
                sessions_user_may_mark(user)
                .filter(
                    academic_year=selected_year,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .select_related(
                    "activity_type",
                    "class_section",
                    "house",
                    "student_group",
                    "subject",
                    "responsible_staff",
                )
                .order_by("-date", "-start_time", "name")
            )
            history = build_student_attendance_history(student, sessions)
            for row in history["marked_rows"]:
                row["mark_url"] = reverse(
                    "admin:school_activitysession_mark_attendance",
                    args=[row["session"].pk],
                )
                audit_params = {
                    "student": student.pk,
                    "session": row["session"].pk,
                }
                if selected_year:
                    audit_params["academic_year"] = selected_year.pk
                if date_from:
                    audit_params["date_from"] = date_from.isoformat()
                if date_to:
                    audit_params["date_to"] = date_to.isoformat()
                row["correction_audit_url"] = (
                    reverse("admin:school_activitysession_attendance_correction_audit")
                    + "?"
                    + urlencode(audit_params)
                )
            for row in history["unmarked_rows"]:
                row["mark_url"] = reverse(
                    "admin:school_activitysession_mark_attendance",
                    args=[row["session"].pk],
                )

        percentage_display = "N/A"
        if history["percentage"] is not None:
            percentage_display = f"{history['percentage']:.1f}".rstrip("0").rstrip(".") + "%"

        context = {
            **self.admin_site.each_context(request),
            "title": f"Attendance history: {student}",
            "opts": self.model._meta,
            "student": student,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "history": history,
            "percentage_display": percentage_display,
            "correction_audit_url": (
                reverse("admin:school_activitysession_attendance_correction_audit")
                + "?"
                + urlencode(
                    {
                        key: value
                        for key, value in {
                            "academic_year": selected_year.pk if selected_year else "",
                            "date_from": date_from.isoformat() if date_from else "",
                            "date_to": date_to.isoformat() if date_to else "",
                            "student": student.pk,
                        }.items()
                        if value != ""
                    }
                )
            ),
        }
        return TemplateResponse(
            request,
            "admin/school/student/attendance_history.html",
            context,
        )


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "staff_designation", "user_is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    list_select_related = ("user", "user__designation")

    @admin.display(description="Designation")
    def staff_designation(self, obj):
        return obj.user.designation

    @admin.display(description="Active", boolean=True)
    def user_is_active(self, obj):
        return obj.user.is_active


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 1
    autocomplete_fields = ("student",)
    verbose_name = "child"
    verbose_name_plural = "children"


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "user_is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
    inlines = (StudentGuardianInline,)

    @admin.display(description="Active", boolean=True)
    def user_is_active(self, obj):
        return obj.user.is_active


@admin.register(StudentGuardian)
class StudentGuardianAdmin(admin.ModelAdmin):
    list_display = ("parent_profile", "student")
    search_fields = (
        "parent_profile__user__username",
        "parent_profile__user__first_name",
        "parent_profile__user__last_name",
        "student__admission_number",
        "student__first_name",
        "student__last_name",
    )
    autocomplete_fields = ("parent_profile", "student")
    list_select_related = ("parent_profile__user", "student")
    list_per_page = 50


class HouseStaffAssignmentInline(admin.TabularInline):
    model = HouseMasterAssignment
    extra = 2
    fields = ("role", "staff", "academic_year")
    autocomplete_fields = ("staff", "academic_year")
    verbose_name = "house teacher"
    verbose_name_plural = "house teachers"


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "attendance_report_link")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)
    inlines = (HouseStaffAssignmentInline,)
    change_list_template = "admin/school/house/change_list.html"
    list_per_page = 200

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["house_list"] = House.objects.order_by("name")
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
            path(
                "house/<int:object_id>/",
                self.admin_site.admin_view(self.house_overview_view),
                name="school_house_house_overview",
            ),
            path(
                "house/<int:object_id>/students/",
                self.admin_site.admin_view(self.house_students_view),
                name="school_house_house_students",
            ),
            path(
                "house/<int:object_id>/attendance/",
                self.admin_site.admin_view(self.house_attendance_view),
                name="school_house_house_attendance",
            ),
            path(
                "house/<int:object_id>/routine/",
                self.admin_site.admin_view(self.house_routine_view),
                name="school_house_house_routine",
            ),
            path(
                "<int:object_id>/attendance-report/",
                self.admin_site.admin_view(self.attendance_report_view),
                name="school_house_attendance_report",
            ),
        ]
        return extra + super().get_urls()

    @admin.display(description="Attendance")
    def attendance_report_link(self, obj):
        url = reverse("admin:school_house_attendance_report", args=[obj.pk])
        return format_html('<a href="{}">Attendance report</a>', url)

    def _house_urls(self, house, selected_year):
        query = f"?academic_year={selected_year.pk}" if selected_year else ""
        return {
            "overview_url": reverse(
                "admin:school_house_house_overview",
                args=[house.pk],
            )
            + query,
            "students_url": reverse(
                "admin:school_house_house_students",
                args=[house.pk],
            )
            + query,
            "attendance_url": reverse(
                "admin:school_house_house_attendance",
                args=[house.pk],
            )
            + query,
            "routine_url": reverse(
                "admin:school_house_house_routine",
                args=[house.pk],
            )
            + query,
        }

    def _house_page_year(self, request):
        years = list(AcademicYear.objects.order_by("-start_date"))
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None
        raw_year = request.GET.get("academic_year") or request.POST.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                selected_year = None
        return years, selected_year

    def _house_students(self, house, selected_year):
        if selected_year is None:
            return Student.objects.none()
        return (
            Student.objects.filter(
                house_memberships__house=house,
                house_memberships__academic_year=selected_year,
            )
            .select_related("class_section", "academic_year")
            .order_by(
                "class_section__grade_number",
                "class_section__section_name",
                "roll_number",
                "last_name",
                "first_name",
            )
        )

    def _save_house_staff(self, year, house, role, staff_id):
        staff_id = (staff_id or "").strip()
        if not staff_id:
            HouseMasterAssignment.objects.filter(
                academic_year=year,
                house=house,
                role=role,
            ).delete()
            return
        staff = User.objects.filter(
            pk=int(staff_id),
            category=UserCategory.STAFF,
        ).first()
        if staff is None:
            return
        HouseMasterAssignment.objects.update_or_create(
            academic_year=year,
            house=house,
            role=role,
            defaults={"staff": staff},
        )

    def house_overview_view(self, request, object_id):
        house = House.objects.filter(pk=object_id).first()
        if house is None:
            raise Http404("House not found.")
        years, selected_year = self._house_page_year(request)
        can_edit = (
            request.user.is_authenticated
            and request.user.is_active
            and request.user.category == UserCategory.ADMINISTRATION
        )
        if request.method == "POST":
            if not can_edit:
                raise PermissionDenied
            if selected_year is None:
                raise Http404("Academic year not found.")
            show_assistant = request.POST.get("show_assistant_house_teacher") == "on"
            house_teacher_id = (request.POST.get("house_teacher") or "").strip()
            assistant_id = (request.POST.get("assistant_house_teacher") or "").strip()
            self._save_house_staff(
                selected_year,
                house,
                HouseStaffRole.HOUSE_TEACHER,
                house_teacher_id,
            )
            if show_assistant:
                if assistant_id and assistant_id == house_teacher_id:
                    messages.error(
                        request,
                        "House teacher and assistant house teacher must be different people.",
                    )
                    HouseMasterAssignment.objects.filter(
                        academic_year=selected_year,
                        house=house,
                        role=HouseStaffRole.ASSISTANT_HOUSE_TEACHER,
                    ).delete()
                else:
                    self._save_house_staff(
                        selected_year,
                        house,
                        HouseStaffRole.ASSISTANT_HOUSE_TEACHER,
                        assistant_id,
                    )
            else:
                HouseMasterAssignment.objects.filter(
                    academic_year=selected_year,
                    house=house,
                    role=HouseStaffRole.ASSISTANT_HOUSE_TEACHER,
                ).delete()
            return HttpResponseRedirect(
                reverse(
                    "admin:school_house_house_overview",
                    args=[house.pk],
                )
                + f"?academic_year={selected_year.pk}"
            )

        assignments = {}
        if selected_year:
            assignments = {
                item.role: item
                for item in HouseMasterAssignment.objects.filter(
                    academic_year=selected_year,
                    house=house,
                ).select_related("staff")
            }
        staff_users = User.objects.filter(category=UserCategory.STAFF).order_by(
            "last_name",
            "first_name",
            "username",
        )
        assistant = assignments.get(HouseStaffRole.ASSISTANT_HOUSE_TEACHER)
        context = {
            **self.admin_site.each_context(request),
            "title": str(house),
            "opts": self.model._meta,
            "house": house,
            "years": years,
            "selected_year": selected_year,
            "staff_users": staff_users,
            "house_teacher": assignments.get(HouseStaffRole.HOUSE_TEACHER),
            "assistant_house_teacher": assistant,
            "show_assistant_house_teacher": assistant is not None,
            "can_edit": can_edit,
            "changelist_url": reverse("admin:school_house_changelist"),
            "current_house_section": "home",
            **self._house_urls(house, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/house/house_overview.html",
            context,
        )

    def house_students_view(self, request, object_id):
        house = House.objects.filter(pk=object_id).first()
        if house is None:
            raise Http404("House not found.")
        years, selected_year = self._house_page_year(request)
        context = {
            **self.admin_site.each_context(request),
            "title": str(house),
            "opts": self.model._meta,
            "house": house,
            "years": years,
            "selected_year": selected_year,
            "students": self._house_students(house, selected_year),
            "changelist_url": reverse("admin:school_house_changelist"),
            "current_house_section": "students",
            **self._house_urls(house, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/house/house_students.html",
            context,
        )

    def house_routine_view(self, request, object_id):
        house = House.objects.filter(pk=object_id).first()
        if house is None:
            raise Http404("House not found.")
        years, selected_year = self._house_page_year(request)
        slots = []
        if selected_year:
            slots = list(
                RoutineSlot.objects.filter(
                    routine__academic_year=selected_year,
                    is_active=True,
                    activity_type__default_audience_kind=AudienceKind.HOUSE,
                )
                .select_related("activity_type", "routine")
                .order_by("sort_order", "start_time")
            )
        context = {
            **self.admin_site.each_context(request),
            "title": str(house),
            "opts": self.model._meta,
            "house": house,
            "years": years,
            "selected_year": selected_year,
            "routine_slots": slots,
            "changelist_url": reverse("admin:school_house_changelist"),
            "current_house_section": "routine",
            **self._house_urls(house, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/house/house_routine.html",
            context,
        )

    def house_attendance_view(self, request, object_id):
        from .attendance_auth import can_change_attendance_status, can_take_attendance

        house = House.objects.filter(pk=object_id).first()
        if house is None:
            raise Http404("House not found.")
        years, selected_year = self._house_page_year(request)
        selected_date = timezone.localdate()
        raw_date = request.GET.get("date") or request.POST.get("date")
        if raw_date:
            try:
                selected_date = date.fromisoformat(raw_date)
            except ValueError:
                selected_date = timezone.localdate()

        students = list(self._house_students(house, selected_year))
        sessions = []
        if selected_year:
            sessions = list(
                ActivitySession.objects.filter(
                    date=selected_date,
                    academic_year=selected_year,
                    audience_kind=AudienceKind.HOUSE,
                    house=house,
                    activity_type__takes_attendance=True,
                )
                .select_related("activity_type", "responsible_staff", "routine_slot")
                .order_by("start_time", "name")
            )

        if request.method == "POST":
            user = request.user
            if (
                not user.is_authenticated
                or not user.is_active
                or user.category == UserCategory.PARENT
            ):
                raise PermissionDenied
            errors = _apply_day_attendance(request, sessions, students)
            if not errors:
                messages.success(request, "Attendance saved.")
                query = urlencode(
                    {
                        "academic_year": selected_year.pk if selected_year else "",
                        "date": selected_date.isoformat(),
                    }
                )
                return HttpResponseRedirect(
                    reverse(
                        "admin:school_house_house_attendance",
                        args=[house.pk],
                    )
                    + f"?{query}"
                )
            messages.error(request, " ".join(errors))

        entries = {
            (entry.activity_session_id, entry.student_id): entry
            for entry in AttendanceEntry.objects.filter(
                activity_session__in=sessions,
                student__in=students,
            ).select_related("taken_by", "updated_by")
        }
        session_editable = {
            session.pk: can_take_attendance(request.user, session)
            or can_change_attendance_status(request.user, session)
            for session in sessions
        }
        rows = []
        for student in students:
            cells = []
            for session in sessions:
                entry = entries.get((session.pk, student.pk))
                posted = (
                    request.POST.get(f"status_{session.pk}_{student.pk}")
                    if request.method == "POST"
                    else None
                )
                cells.append(
                    {
                        "session": session,
                        "entry": entry,
                        "status": posted
                        if posted is not None
                        else (entry.status if entry else ""),
                        "editable": session_editable.get(session.pk, False),
                    }
                )
            rows.append({"student": student, "cells": cells})

        can_save = any(session_editable.values())
        context = {
            **self.admin_site.each_context(request),
            "title": str(house),
            "opts": self.model._meta,
            "house": house,
            "years": years,
            "selected_year": selected_year,
            "selected_date": selected_date,
            "sessions": sessions,
            "rows": rows,
            "status_choices": AttendanceStatus.choices,
            "can_save": can_save,
            "changelist_url": reverse("admin:school_house_changelist"),
            "current_house_section": "attendance",
            **self._house_urls(house, selected_year),
        }
        return TemplateResponse(
            request,
            "admin/school/house/house_attendance.html",
            context,
        )

    def _forbidden_report(self, request, message):
        context = {
            **self.admin_site.each_context(request),
            "title": "House attendance report",
            "message": message,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/mark_attendance_denied.html",
            context,
            status=403,
        )

    def _format_present_rate(self, percentage):
        if percentage is None:
            return "N/A"
        return f"{percentage:.1f}".rstrip("0").rstrip(".") + "%"

    def attendance_report_view(self, request, object_id):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import build_house_attendance_report

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_report(
                request,
                "You are not authorized to view the house attendance report.",
            )

        house = House.objects.filter(pk=object_id).first()
        if house is None:
            raise Http404("House not found.")

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None

        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid start date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid end date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        report = build_house_attendance_report(house, selected_year, [])
        if selected_year and date_from and date_to:
            sessions = (
                sessions_user_may_mark(user)
                .filter(
                    audience_kind=AudienceKind.HOUSE,
                    house=house,
                    academic_year=selected_year,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .select_related("activity_type", "house")
                .order_by("date", "start_time", "name")
            )
            report = build_house_attendance_report(house, selected_year, sessions)

        for row in report["student_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])
            row["history_url"] = reverse(
                "admin:school_student_attendance_history",
                args=[row["student"].pk],
            )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Attendance report: {house}",
            "opts": self.model._meta,
            "house": house,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "report": report,
            "percentage_display": self._format_present_rate(report["percentage"]),
        }
        return TemplateResponse(
            request,
            "admin/school/house/attendance_report.html",
            context,
        )


@admin.register(StudentHouseMembership)
class StudentHouseMembershipAdmin(admin.ModelAdmin):
    list_display = ("student", "house", "academic_year")
    list_filter = ("academic_year", "house")
    search_fields = (
        "student__admission_number",
        "student__first_name",
        "student__last_name",
        "house__name",
    )
    autocomplete_fields = ("student", "house", "academic_year")
    list_select_related = ("student", "house", "academic_year")
    ordering = ("-academic_year", "house", "student")
    list_per_page = 50


@admin.register(StudentClassMembership)
class StudentClassMembershipAdmin(admin.ModelAdmin):
    list_display = ("student", "class_section", "academic_year")
    list_filter = ("academic_year", "class_section")
    search_fields = (
        "student__admission_number",
        "student__first_name",
        "student__last_name",
        "class_section__display_name",
        "class_section__grade_name",
        "class_section__section_name",
    )
    autocomplete_fields = ("student", "class_section", "academic_year")
    list_select_related = ("student", "class_section", "academic_year")
    ordering = ("-academic_year", "class_section", "student")
    list_per_page = 50


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "subject", "class_section", "academic_year")
    list_filter = ("academic_year", "class_section", "subject")
    search_fields = (
        "teacher__user__username",
        "teacher__user__first_name",
        "teacher__user__last_name",
        "subject__name",
        "subject__code",
        "class_section__display_name",
    )
    autocomplete_fields = ("teacher", "subject", "class_section", "academic_year")
    list_select_related = (
        "teacher__user",
        "subject",
        "class_section",
        "academic_year",
    )
    ordering = ("-academic_year", "class_section", "subject")
    list_per_page = 50


@admin.register(ClassTeacherAssignment)
class ClassTeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "class_section", "academic_year")
    list_filter = ("academic_year", "class_section")
    search_fields = (
        "teacher__user__username",
        "teacher__user__first_name",
        "teacher__user__last_name",
        "class_section__display_name",
    )
    autocomplete_fields = ("teacher", "class_section", "academic_year")
    list_select_related = ("teacher__user", "class_section", "academic_year")
    ordering = ("-academic_year", "class_section")


@admin.register(HouseMasterAssignment)
class HouseMasterAssignmentAdmin(admin.ModelAdmin):
    list_display = ("staff", "role", "staff_designation", "house", "academic_year")
    list_filter = ("academic_year", "house", "role")
    search_fields = (
        "staff__username",
        "staff__first_name",
        "staff__last_name",
        "house__name",
    )
    autocomplete_fields = ("staff", "house", "academic_year")
    list_select_related = ("staff", "staff__designation", "house", "academic_year")
    ordering = ("-academic_year", "house", "role")

    @admin.display(description="Designation")
    def staff_designation(self, obj):
        return obj.staff.designation


class StudentGroupMembershipInline(admin.TabularInline):
    model = StudentGroupMembership
    extra = 1
    autocomplete_fields = ("student",)


class ActivitySessionParticipantInline(admin.TabularInline):
    model = ActivitySessionParticipant
    extra = 1
    autocomplete_fields = ("student",)

    def has_add_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_delete_permission(request, obj)


class AttendanceRevisionInline(admin.TabularInline):
    model = AttendanceRevision
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "changed_by", "changed_at", "reason")
    fields = ("old_status", "new_status", "changed_by", "changed_at", "reason")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "default_audience_kind",
        "requires_subject",
        "takes_attendance",
        "is_active",
    )
    list_filter = ("is_active", "requires_subject", "takes_attendance", "default_audience_kind")
    search_fields = ("name", "code")
    ordering = ("name",)


@admin.register(DutyType)
class DutyTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "unique_per_day", "is_active")
    list_filter = ("is_active", "unique_per_day")
    search_fields = ("name", "code")
    ordering = ("name",)


def _posted_fk_id(form, field_name):
    key = form.add_prefix(field_name)
    raw = form.data.get(key) if form.data else None
    if raw not in (None, ""):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if form.instance.pk:
        return getattr(form.instance, f"{field_name}_id")
    return None


class RoutineSlotInline(admin.TabularInline):
    model = RoutineSlot
    extra = 3
    ordering = ("sort_order", "start_time")
    autocomplete_fields = ("activity_type",)
    fields = ("sort_order", "activity_type", "name", "start_time", "end_time", "is_active")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "activity_type":
            kwargs["queryset"] = ActivityType.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ClassTimetableEntryForm(forms.ModelForm):
    class Meta:
        model = ClassTimetableEntry
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        year_id = _posted_fk_id(self, "academic_year")
        class_id = _posted_fk_id(self, "class_section")
        subject_id = _posted_fk_id(self, "subject")

        assignments = TeachingAssignment.objects.all()
        if year_id:
            assignments = assignments.filter(academic_year_id=year_id)

        class_assignments = assignments
        if class_id:
            class_assignments = assignments.filter(class_section_id=class_id)

        self.fields["class_section"].queryset = ClassSection.objects.filter(
            is_active=True,
            pk__in=assignments.values("class_section"),
        )
        subject_assignments = class_assignments if class_id else assignments
        self.fields["subject"].queryset = Subject.objects.filter(
            is_active=True,
            pk__in=subject_assignments.values("subject"),
        )
        teacher_assignments = class_assignments
        if subject_id:
            teacher_assignments = teacher_assignments.filter(subject_id=subject_id)
        self.fields["teacher"].queryset = TeacherProfile.objects.filter(
            user__is_active=True,
            user__category=UserCategory.STAFF,
            pk__in=teacher_assignments.values("teacher"),
        ).select_related("user")

        slots = RoutineSlot.objects.filter(
            routine__is_active=True,
            is_active=True,
            activity_type__is_active=True,
            activity_type__default_audience_kind=AudienceKind.CLASS,
        ).select_related("routine", "activity_type")
        if year_id:
            slots = slots.filter(routine__academic_year_id=year_id)
        self.fields["routine_slot"].queryset = slots


class SchoolCalendarDayForm(forms.ModelForm):
    class Meta:
        model = SchoolCalendarDay
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        year_id = _posted_fk_id(self, "academic_year")
        routines = Routine.objects.filter(is_active=True)
        if year_id:
            routines = routines.filter(academic_year_id=year_id)
        self.fields["routine"].queryset = routines


@admin.register(Routine)
class RoutineAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "is_active")
    list_filter = ("academic_year", "is_active")
    search_fields = ("name",)
    autocomplete_fields = ("academic_year",)
    ordering = ("academic_year", "name")
    inlines = (RoutineSlotInline,)


@admin.register(RoutineSlot)
class RoutineSlotAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "routine",
        "activity_type",
        "start_time",
        "end_time",
        "sort_order",
        "is_active",
    )
    list_filter = ("routine__academic_year", "routine", "activity_type", "is_active")
    search_fields = ("name", "routine__name", "activity_type__name")
    autocomplete_fields = ("routine", "activity_type")
    ordering = ("routine", "sort_order")
    list_select_related = ("routine", "activity_type")


@admin.register(SchoolCalendarDay)
class SchoolCalendarDayAdmin(admin.ModelAdmin):
    form = SchoolCalendarDayForm
    list_display = (
        "date",
        "academic_year",
        "routine",
        "note",
        "sessions_on_this_date",
        "attendance_overview_link",
        "absences_this_date_link",
    )
    list_filter = ("academic_year", "routine")
    date_hierarchy = "date"
    list_editable = ("routine", "note")
    search_fields = ("note", "routine__name")
    autocomplete_fields = ("academic_year",)
    ordering = ("-date",)
    list_select_related = ("academic_year", "routine")
    actions = ("generate_daily_sessions",)

    @admin.display(description="Sessions")
    def sessions_on_this_date(self, obj):
        url = reverse("admin:school_activitysession_changelist")
        return format_html(
            '<a href="{}?date__exact={}">Sessions on this date</a>',
            url,
            obj.date.isoformat(),
        )

    @admin.display(description="Attendance")
    def attendance_overview_link(self, obj):
        url = reverse("admin:school_activitysession_attendance_overview")
        return format_html(
            '<a href="{}?date={}">Attendance overview</a>',
            url,
            obj.date.isoformat(),
        )

    @admin.display(description="Absences")
    def absences_this_date_link(self, obj):
        url = reverse("admin:school_activitysession_absence_register")
        return format_html(
            '<a href="{}?date={}">Absences this date</a>',
            url,
            obj.date.isoformat(),
        )

    @admin.action(description="Generate daily sessions")
    def generate_daily_sessions(self, request, queryset):
        from .generation import generate_sessions_for_calendar_days

        days = queryset.select_related("academic_year", "routine")
        results = generate_sessions_for_calendar_days(days)
        created = sum(item.created for item in results)
        existed = sum(item.already_existed for item in results)
        skipped = sum(item.skipped for item in results)
        warnings = [item.summary() for item in results]
        level = messages.WARNING if any(item.errors or item.warnings for item in results) else messages.SUCCESS
        self.message_user(
            request,
            " | ".join(warnings)
            if warnings
            else f"Created {created}, already existed {existed}, skipped {skipped}.",
            level=level,
        )


@admin.register(ClassTimetableEntry)
class ClassTimetableEntryAdmin(admin.ModelAdmin):
    form = ClassTimetableEntryForm
    list_display = (
        "academic_year",
        "routine",
        "routine_slot",
        "class_section",
        "subject",
        "teacher",
    )
    list_filter = (
        "academic_year",
        "routine_slot__routine",
        "class_section",
        "routine_slot",
        "subject",
    )
    search_fields = (
        "class_section__display_name",
        "subject__name",
        "teacher__user__username",
        "teacher__user__first_name",
        "teacher__user__last_name",
        "routine_slot__name",
        "routine_slot__routine__name",
    )
    autocomplete_fields = ("academic_year",)
    list_select_related = (
        "academic_year",
        "class_section",
        "routine_slot__routine",
        "routine_slot__activity_type",
        "subject",
        "teacher__user",
    )
    ordering = ("academic_year", "class_section", "routine_slot__sort_order")

    @admin.display(description="Routine", ordering="routine_slot__routine")
    def routine(self, obj):
        return obj.routine_slot.routine


@admin.register(StudentGroup)
class StudentGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "is_active")
    list_filter = ("academic_year", "is_active")
    search_fields = ("name",)
    autocomplete_fields = ("academic_year",)
    inlines = (StudentGroupMembershipInline,)


@admin.register(StudentGroupMembership)
class StudentGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "student")
    list_filter = ("group__academic_year", "group")
    search_fields = (
        "group__name",
        "student__admission_number",
        "student__first_name",
        "student__last_name",
    )
    autocomplete_fields = ("group", "student")
    list_select_related = ("group", "student")


@admin.register(ActivitySession)
class ActivitySessionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "name",
        "activity_type",
        "audience_kind",
        "class_section",
        "house",
        "student_group",
        "subject",
        "responsible_staff",
        "start_time",
        "end_time",
        "mark_attendance_link",
    )
    list_filter = ("academic_year", "activity_type", "audience_kind", "date")
    date_hierarchy = "date"
    search_fields = (
        "name",
        "responsible_staff__username",
        "responsible_staff__first_name",
        "responsible_staff__last_name",
        "class_section__display_name",
        "house__name",
        "student_group__name",
        "subject__name",
    )
    autocomplete_fields = (
        "academic_year",
        "routine_slot",
        "activity_type",
        "class_section",
        "house",
        "student_group",
        "subject",
        "responsible_staff",
        "teaching_assignment",
    )
    list_select_related = (
        "academic_year",
        "activity_type",
        "class_section",
        "house",
        "student_group",
        "subject",
        "responsible_staff",
        "routine_slot",
    )
    inlines = (ActivitySessionParticipantInline,)
    ordering = ("-date", "start_time")
    list_per_page = 50

    def get_queryset(self, request):
        from .attendance_auth import sessions_user_may_mark

        qs = super().get_queryset(request)
        if request.user.category == UserCategory.ADMINISTRATION:
            return qs
        return qs.filter(pk__in=sessions_user_may_mark(request.user))

    def has_add_permission(self, request):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_delete_permission(request, obj)

    change_list_template = "admin/school/activitysession/change_list.html"

    def changelist_view(self, request, extra_context=None):
        self._request = request
        extra_context = extra_context or {}
        extra_context["school_attendance_report_url"] = reverse(
            "admin:school_activitysession_school_attendance_report"
        )
        extra_context["attendance_coverage_url"] = reverse(
            "admin:school_activitysession_attendance_coverage"
        )
        extra_context["correction_audit_url"] = reverse(
            "admin:school_activitysession_attendance_correction_audit"
        )
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
            path(
                "attendance-overview/",
                self.admin_site.admin_view(self.attendance_overview_view),
                name="school_activitysession_attendance_overview",
            ),
            path(
                "absence-register/",
                self.admin_site.admin_view(self.absence_register_view),
                name="school_activitysession_absence_register",
            ),
            path(
                "school-attendance-report/",
                self.admin_site.admin_view(self.school_attendance_report_view),
                name="school_activitysession_school_attendance_report",
            ),
            path(
                "attendance-coverage/",
                self.admin_site.admin_view(self.attendance_coverage_view),
                name="school_activitysession_attendance_coverage",
            ),
            path(
                "attendance-correction-audit/",
                self.admin_site.admin_view(self.attendance_correction_audit_view),
                name="school_activitysession_attendance_correction_audit",
            ),
            path(
                "<int:object_id>/mark-attendance/",
                self.admin_site.admin_view(self.mark_attendance_view),
                name="school_activitysession_mark_attendance",
            ),
        ]
        return extra + super().get_urls()

    @admin.display(description="Attendance")
    def mark_attendance_link(self, obj):
        request = getattr(self, "_request", None)
        if request is None:
            return ""
        if not obj.activity_type_id or not obj.activity_type.takes_attendance:
            return ""
        from .attendance_auth import can_take_attendance

        if not can_take_attendance(request.user, obj):
            return ""
        url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[obj.pk],
        )
        return format_html('<a href="{}">Mark attendance</a>', url)

    def _forbidden_attendance(self, request, message):
        context = {
            **self.admin_site.each_context(request),
            "title": "Attendance cannot be marked",
            "message": message,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/mark_attendance_denied.html",
            context,
            status=403,
        )

    def _overview_target_label(self, session):
        if session.audience_kind == AudienceKind.CLASS:
            return str(session.class_section) if session.class_section_id else "Class"
        if session.audience_kind == AudienceKind.HOUSE:
            return str(session.house) if session.house_id else "House"
        if session.audience_kind == AudienceKind.STUDENT_GROUP:
            return str(session.student_group) if session.student_group_id else "Student group"
        if session.audience_kind == AudienceKind.SCHOOL:
            return "Whole school"
        if session.audience_kind == AudienceKind.SELECTED_STUDENTS:
            return "Selected students"
        return session.get_audience_kind_display()

    def attendance_overview_view(self, request):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import overview_rows_for_sessions

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_attendance(
                request,
                "You are not authorized to view the attendance overview.",
            )

        date_error = ""
        raw_date = request.GET.get("date")
        if raw_date:
            try:
                selected_date = date.fromisoformat(raw_date)
            except ValueError:
                selected_date = None
                date_error = "Enter a valid date."
        else:
            selected_date = timezone.localdate()

        calendar_day = None
        notice = date_error
        rows = []
        if selected_date is not None:
            calendar_day = SchoolCalendarDay.objects.filter(date=selected_date).first()
            if calendar_day is None:
                notice = "No school calendar day for this date. Sessions are not generated from this page."
            else:
                sessions = (
                    sessions_user_may_mark(user)
                    .filter(date=selected_date)
                    .select_related(
                        "activity_type",
                        "class_section",
                        "house",
                        "student_group",
                        "responsible_staff",
                        "routine_slot",
                    )
                    .order_by("start_time", "name")
                )
                for item in overview_rows_for_sessions(sessions):
                    session = item["session"]
                    mark_url = reverse(
                        "admin:school_activitysession_mark_attendance",
                        args=[session.pk],
                    )
                    item["target"] = self._overview_target_label(session)
                    item["mark_url"] = mark_url
                    rows.append(item)
                if not rows:
                    notice = "No attendance-capable sessions for this date."

        context = {
            **self.admin_site.each_context(request),
            "title": "Daily attendance overview",
            "opts": self.model._meta,
            "selected_date": selected_date,
            "calendar_day": calendar_day,
            "notice": notice,
            "rows": rows,
            "absence_register_url": (
                reverse("admin:school_activitysession_absence_register")
                + f"?date={selected_date.isoformat()}"
                if selected_date
                else ""
            ),
            "school_attendance_report_url": self._school_report_url_for_overview(
                selected_date,
                calendar_day,
            ),
            "attendance_coverage_url": self._coverage_url_for_overview(
                selected_date,
                calendar_day,
            ),
            "correction_audit_url": self._audit_url_for_overview(
                selected_date,
                calendar_day,
            ),
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/attendance_overview.html",
            context,
        )

    def absence_register_view(self, request):
        from .attendance_auth import sessions_user_may_mark

        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_attendance(
                request,
                "You are not authorized to view the absence register.",
            )

        exception_statuses = (
            AttendanceStatus.ABSENT,
            AttendanceStatus.LATE,
            AttendanceStatus.LEAVE,
        )
        raw_statuses = request.GET.getlist("status")
        if raw_statuses:
            selected_statuses = tuple(
                status for status in raw_statuses if status in exception_statuses
            )
        else:
            selected_statuses = exception_statuses

        date_error = ""
        raw_date = request.GET.get("date")
        if raw_date:
            try:
                selected_date = date.fromisoformat(raw_date)
            except ValueError:
                selected_date = None
                date_error = "Enter a valid date."
        else:
            selected_date = timezone.localdate()

        calendar_day = None
        notice = date_error
        entries = []
        if selected_date is not None:
            calendar_day = SchoolCalendarDay.objects.filter(date=selected_date).first()
            if calendar_day is None:
                notice = (
                    "No school calendar day for this date. "
                    "Sessions are not generated from this page."
                )
            elif selected_statuses:
                sessions = sessions_user_may_mark(user).filter(
                    date=selected_date,
                    activity_type__takes_attendance=True,
                )
                entries = list(
                    AttendanceEntry.objects.filter(
                        activity_session__in=sessions,
                        status__in=selected_statuses,
                    )
                    .select_related(
                        "activity_session",
                        "activity_session__activity_type",
                        "activity_session__class_section",
                        "activity_session__house",
                        "activity_session__student_group",
                        "student",
                        "taken_by",
                        "updated_by",
                    )
                    .order_by(
                        "activity_session__start_time",
                        "activity_session__name",
                        "student__roll_number",
                        "student__last_name",
                        "student__first_name",
                    )
                )
                if not entries:
                    labels = [
                        dict(AttendanceStatus.choices)[status]
                        for status in selected_statuses
                    ]
                    notice = (
                        f"No {'/'.join(labels)} marks on your authorized sessions."
                    )
            else:
                notice = "No Absent/Late/Leave marks on your authorized sessions."

        rows = []
        for entry in entries:
            session = entry.activity_session
            rows.append(
                {
                    "entry": entry,
                    "session": session,
                    "target": self._overview_target_label(session),
                    "mark_url": reverse(
                        "admin:school_activitysession_mark_attendance",
                        args=[session.pk],
                    ),
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Daily absence register",
            "opts": self.model._meta,
            "selected_date": selected_date,
            "calendar_day": calendar_day,
            "notice": notice,
            "rows": rows,
            "selected_statuses": selected_statuses,
            "status_choices": (
                (AttendanceStatus.ABSENT, "Absent"),
                (AttendanceStatus.LATE, "Late"),
                (AttendanceStatus.LEAVE, "Leave"),
            ),
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/absence_register.html",
            context,
        )

    def _format_present_rate(self, percentage):
        if percentage is None:
            return "N/A"
        return f"{percentage:.1f}".rstrip("0").rstrip(".") + "%"

    def _school_report_query(self, selected_year, date_from, date_to):
        params = {}
        if selected_year:
            params["academic_year"] = selected_year.pk
        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        return urlencode(params)

    def _school_report_url_for_overview(self, selected_date, calendar_day):
        if selected_date is None:
            return ""
        params = {
            "date_from": selected_date.isoformat(),
            "date_to": selected_date.isoformat(),
        }
        if calendar_day is not None:
            params["academic_year"] = calendar_day.academic_year_id
        return (
            reverse("admin:school_activitysession_school_attendance_report")
            + "?"
            + urlencode(params)
        )

    def _coverage_url_for_overview(self, selected_date, calendar_day):
        if selected_date is None:
            return ""
        params = {
            "date_from": selected_date.isoformat(),
            "date_to": selected_date.isoformat(),
        }
        if calendar_day is not None:
            params["academic_year"] = calendar_day.academic_year_id
        return (
            reverse("admin:school_activitysession_attendance_coverage")
            + "?"
            + urlencode(params)
        )

    def _audit_url_for_overview(self, selected_date, calendar_day):
        if selected_date is None:
            return ""
        params = {
            "date_from": selected_date.isoformat(),
            "date_to": selected_date.isoformat(),
        }
        if calendar_day is not None:
            params["academic_year"] = calendar_day.academic_year_id
        return (
            reverse("admin:school_activitysession_attendance_correction_audit")
            + "?"
            + urlencode(params)
        )

    def attendance_correction_audit_view(self, request):
        from django.contrib.auth import get_user_model

        from .attendance_auth import sessions_user_may_mark

        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_attendance(
                request,
                "You are not authorized to view the attendance correction audit.",
            )

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None

        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        valid_statuses = {choice[0] for choice in AttendanceStatus.choices}
        selected_session_id = None
        selected_changed_by_id = None
        selected_student_id = None
        selected_status = (request.GET.get("status") or "").strip()
        status_invalid = bool(selected_status and selected_status not in valid_statuses)
        if status_invalid:
            selected_status = ""

        raw_session = request.GET.get("session")
        if raw_session:
            try:
                selected_session_id = int(raw_session)
            except (TypeError, ValueError):
                selected_session_id = False
        raw_changed_by = request.GET.get("changed_by")
        if raw_changed_by:
            try:
                selected_changed_by_id = int(raw_changed_by)
            except (TypeError, ValueError):
                selected_changed_by_id = False
        raw_student = request.GET.get("student")
        if raw_student:
            try:
                selected_student_id = int(raw_student)
            except (TypeError, ValueError):
                selected_student_id = False

        session_options = []
        changed_by_options = []
        student_options = []
        page_obj = None
        result_count = 0
        rows = []
        if selected_year and date_from and date_to:
            authorized_sessions = sessions_user_may_mark(user).filter(
                academic_year=selected_year,
                date__gte=date_from,
                date__lte=date_to,
            )
            revisions = AttendanceRevision.objects.filter(
                entry__activity_session__in=authorized_sessions,
            )
            session_options = list(
                authorized_sessions.filter(
                    pk__in=revisions.values("entry__activity_session_id"),
                )
                .select_related("activity_type", "class_section", "house", "student_group")
                .order_by("date", "start_time", "name")
            )
            student_options = list(
                Student.objects.filter(
                    pk__in=revisions.values("entry__student_id"),
                ).order_by("last_name", "first_name", "admission_number")
            )
            User = get_user_model()
            changed_by_options = list(
                User.objects.filter(
                    pk__in=revisions.values("changed_by_id"),
                ).order_by("username")
            )

            if (
                selected_session_id is False
                or selected_changed_by_id is False
                or selected_student_id is False
                or status_invalid
            ):
                revisions = revisions.none()
            else:
                if selected_session_id is not None:
                    if authorized_sessions.filter(pk=selected_session_id).exists():
                        revisions = revisions.filter(
                            entry__activity_session_id=selected_session_id
                        )
                    else:
                        revisions = revisions.none()
                if selected_changed_by_id is not None:
                    allowed_changers = {option.pk for option in changed_by_options}
                    if selected_changed_by_id in allowed_changers:
                        revisions = revisions.filter(
                            changed_by_id=selected_changed_by_id
                        )
                    else:
                        revisions = revisions.none()
                if selected_student_id is not None:
                    allowed_students = {option.pk for option in student_options}
                    if selected_student_id in allowed_students:
                        revisions = revisions.filter(
                            entry__student_id=selected_student_id
                        )
                    else:
                        revisions = revisions.none()
                if selected_status:
                    revisions = revisions.filter(new_status=selected_status)

            revisions = (
                revisions.select_related(
                    "entry__activity_session__activity_type",
                    "entry__activity_session__class_section",
                    "entry__activity_session__house",
                    "entry__activity_session__student_group",
                    "entry__student",
                    "changed_by",
                )
                .order_by("-changed_at", "-pk")
            )
            paginator = Paginator(revisions, 50)
            raw_page = request.GET.get("page") or 1
            try:
                page_obj = paginator.page(raw_page)
            except PageNotAnInteger:
                page_obj = paginator.page(1)
            except EmptyPage:
                page_obj = paginator.page(paginator.num_pages)
            result_count = paginator.count
            for revision in page_obj.object_list:
                session = revision.entry.activity_session
                rows.append(
                    {
                        "revision": revision,
                        "session": session,
                        "target": self._overview_target_label(session),
                    }
                )
            if result_count == 0 and not notice:
                notice = "No attendance corrections found in this range."

        query_params = {}
        if selected_year:
            query_params["academic_year"] = selected_year.pk
        if date_from:
            query_params["date_from"] = date_from.isoformat()
        if date_to:
            query_params["date_to"] = date_to.isoformat()
        if selected_session_id not in (None, False):
            query_params["session"] = selected_session_id
        if selected_changed_by_id not in (None, False):
            query_params["changed_by"] = selected_changed_by_id
        if selected_student_id not in (None, False):
            query_params["student"] = selected_student_id
        if selected_status:
            query_params["status"] = selected_status
        filter_query = urlencode(query_params)

        context = {
            **self.admin_site.each_context(request),
            "title": "Attendance correction audit",
            "opts": self.model._meta,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "rows": rows,
            "page_obj": page_obj,
            "result_count": result_count,
            "filter_query": filter_query,
            "session_options": session_options,
            "changed_by_options": changed_by_options,
            "student_options": student_options,
            "selected_session_id": selected_session_id if selected_session_id not in (None, False) else "",
            "selected_changed_by_id": selected_changed_by_id if selected_changed_by_id not in (None, False) else "",
            "selected_student_id": selected_student_id if selected_student_id not in (None, False) else "",
            "selected_status": selected_status or "",
            "status_choices": AttendanceStatus.choices,
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/attendance_correction_audit.html",
            context,
        )

    def attendance_coverage_view(self, request):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import overview_rows_for_sessions

        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_attendance(
                request,
                "You are not authorized to view attendance coverage.",
            )

        status_choices = (
            ("not_started", "Not started"),
            ("partial", "Partial"),
            ("complete", "Complete"),
            ("empty_roster", "Empty roster"),
        )
        status_labels = dict(status_choices)
        default_statuses = ("not_started", "partial", "empty_roster")
        raw_statuses = request.GET.getlist("status")
        selected_statuses = tuple(
            status for status in raw_statuses if status in status_labels
        )
        if not selected_statuses:
            selected_statuses = default_statuses
        selected_labels = {status_labels[status] for status in selected_statuses}

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None

        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        all_rows = []
        rows = []
        summary = {
            "total": 0,
            "not_started": 0,
            "partial": 0,
            "complete": 0,
            "empty_roster": 0,
        }
        status_to_key = {
            "Not started": "not_started",
            "Partial": "partial",
            "Complete": "complete",
            "Empty roster": "empty_roster",
        }
        if selected_year and date_from and date_to:
            sessions = (
                sessions_user_may_mark(user)
                .filter(
                    academic_year=selected_year,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .select_related(
                    "activity_type",
                    "class_section",
                    "house",
                    "student_group",
                    "responsible_staff",
                    "routine_slot",
                )
                .order_by("date", "start_time", "name")
            )
            all_rows = overview_rows_for_sessions(sessions)
            summary["total"] = len(all_rows)
            for item in all_rows:
                key = status_to_key.get(item["status"])
                if key:
                    summary[key] += 1
                session = item["session"]
                item["target"] = self._overview_target_label(session)
                item["mark_url"] = reverse(
                    "admin:school_activitysession_mark_attendance",
                    args=[session.pk],
                )
            rows = [item for item in all_rows if item["status"] in selected_labels]
            if not all_rows and not notice:
                notice = "No authorized attendance-capable sessions in this range."
            elif all_rows and not rows and not notice:
                notice = "No sessions match the selected coverage statuses."

        context = {
            **self.admin_site.each_context(request),
            "title": "Attendance coverage",
            "opts": self.model._meta,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "rows": rows,
            "summary": summary,
            "selected_statuses": selected_statuses,
            "status_choices": status_choices,
            "correction_audit_url": (
                reverse("admin:school_activitysession_attendance_correction_audit")
                + (
                    f"?{self._school_report_query(selected_year, date_from, date_to)}"
                    if selected_year and date_from and date_to
                    else ""
                )
            ),
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/attendance_coverage.html",
            context,
        )

    def school_attendance_report_view(self, request):
        from .attendance_auth import sessions_user_may_mark
        from .attendance_roster import build_school_attendance_report

        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])

        user = request.user
        if (
            not user.is_authenticated
            or not user.is_active
            or user.category == UserCategory.PARENT
        ):
            return self._forbidden_attendance(
                request,
                "You are not authorized to view the school attendance report.",
            )

        years = list(AcademicYear.objects.order_by("-start_date"))
        notice = ""
        selected_year = AcademicYear.objects.filter(is_current=True).first()
        if selected_year is None:
            selected_year = years[0] if years else None

        raw_year = request.GET.get("academic_year")
        if raw_year:
            try:
                selected_year = AcademicYear.objects.get(pk=int(raw_year))
            except (AcademicYear.DoesNotExist, TypeError, ValueError):
                notice = "Enter a valid academic year."
                selected_year = None

        date_from = selected_year.start_date if selected_year else None
        date_to = selected_year.end_date if selected_year else None
        raw_from = request.GET.get("date_from")
        raw_to = request.GET.get("date_to")
        if raw_from:
            try:
                date_from = date.fromisoformat(raw_from)
            except ValueError:
                notice = "Enter a valid date."
                date_from = None
        if raw_to:
            try:
                date_to = date.fromisoformat(raw_to)
            except ValueError:
                notice = "Enter a valid date."
                date_to = None
        if date_from and date_to and date_from > date_to:
            notice = "The start date must be on or before the end date."
            date_from = None
            date_to = None

        report = build_school_attendance_report([])
        if selected_year and date_from and date_to:
            sessions = (
                sessions_user_may_mark(user)
                .filter(
                    academic_year=selected_year,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .select_related(
                    "activity_type",
                    "class_section",
                    "house",
                    "student_group",
                )
                .order_by("date", "start_time", "name")
            )
            report = build_school_attendance_report(sessions)
            if not report["session_count"] and not notice:
                notice = "No authorized attendance-capable sessions in this range."

        query = self._school_report_query(selected_year, date_from, date_to)
        for row in report["class_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])
            row["report_url"] = (
                reverse(
                    "admin:school_classsection_attendance_report",
                    args=[row["class_section"].pk],
                )
                + f"?{query}"
            )
        for row in report["house_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])
            row["report_url"] = (
                reverse(
                    "admin:school_house_attendance_report",
                    args=[row["house"].pk],
                )
                + f"?{query}"
            )
        for row in report["activity_type_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])
        for row in report["audience_kind_rows"]:
            row["percentage_display"] = self._format_present_rate(row["percentage"])

        context = {
            **self.admin_site.each_context(request),
            "title": "School attendance report",
            "opts": self.model._meta,
            "years": years,
            "selected_year": selected_year,
            "date_from": date_from,
            "date_to": date_to,
            "notice": notice,
            "report": report,
            "percentage_display": self._format_present_rate(report["percentage"]),
            "attendance_coverage_url": (
                reverse("admin:school_activitysession_attendance_coverage")
                + (f"?{query}" if query else "")
            ),
            "correction_audit_url": (
                reverse("admin:school_activitysession_attendance_correction_audit")
                + (f"?{query}" if query else "")
            ),
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/school_attendance_report.html",
            context,
        )

    def _audience_label(self, session):
        if session.class_section_id:
            return str(session.class_section)
        if session.house_id:
            return str(session.house)
        if session.student_group_id:
            return str(session.student_group)
        if session.audience_kind == AudienceKind.SCHOOL:
            return "Whole school"
        if session.audience_kind == AudienceKind.SELECTED_STUDENTS:
            return "Selected students"
        return session.get_audience_kind_display()

    def _attendance_row(self, request, student, entry, posted_status, posted_notes, session=None):
        latest = None
        audit_url = ""
        if entry is not None:
            revisions = list(entry.revisions.all())
            latest = revisions[0] if revisions else None
            if session is not None:
                audit_url = (
                    reverse("admin:school_activitysession_attendance_correction_audit")
                    + "?"
                    + urlencode(
                        {
                            "academic_year": session.academic_year_id,
                            "date_from": session.date.isoformat(),
                            "date_to": session.date.isoformat(),
                            "session": session.pk,
                            "student": student.pk,
                        }
                    )
                )
        if posted_status is None:
            posted_status = entry.status if entry is not None else ""
        if posted_notes is None:
            posted_notes = entry.notes if entry is not None else ""
        return {
            "student": student,
            "entry": entry,
            "latest_revision": latest,
            "correction_audit_url": audit_url,
            "posted_status": posted_status,
            "posted_notes": posted_notes,
        }

    def _mark_attendance_context(self, request, session, errors=None, posted=None):
        from .attendance_roster import orphan_entries_for_session, students_for_session

        roster = list(students_for_session(session))
        roster_ids = [student.pk for student in roster]
        entries = {
            entry.student_id: entry
            for entry in AttendanceEntry.objects.filter(activity_session=session)
            .select_related("student", "taken_by", "updated_by")
            .prefetch_related("revisions")
        }
        posted = posted or {}
        roster_rows = []
        for student in roster:
            entry = entries.get(student.pk)
            roster_rows.append(
                self._attendance_row(
                    request,
                    student,
                    entry,
                    posted.get(f"status_{student.pk}"),
                    posted.get(f"notes_{student.pk}"),
                    session=session,
                )
            )
        orphan_rows = []
        for entry in orphan_entries_for_session(session, roster_ids):
            entry = entries.get(entry.student_id, entry)
            orphan_rows.append(
                self._attendance_row(
                    request,
                    entry.student,
                    entry,
                    posted.get(f"status_{entry.student_id}"),
                    posted.get(f"notes_{entry.student_id}"),
                    session=session,
                )
            )
        marked_on_roster = sum(1 for row in roster_rows if row["entry"] is not None)
        title = (
            f"Attendance: {session.name} — {self._audience_label(session)} — "
            f"{marked_on_roster}/{len(roster)} marked"
        )
        return {
            **self.admin_site.each_context(request),
            "title": title,
            "opts": self.model._meta,
            "session": session,
            "roster_rows": roster_rows,
            "orphan_rows": orphan_rows,
            "roster_count": len(roster),
            "marked_count": marked_on_roster,
            "status_choices": AttendanceStatus.choices,
            "errors": errors or [],
            "change_reason": posted.get("change_reason", ""),
        }

    def _apply_mark_attendance(self, request, session):
        from .attendance_auth import can_change_attendance_status, can_take_attendance
        from .attendance_roster import orphan_entries_for_session, students_for_session

        action = request.POST.get("action", "save")
        save_unmarked = action == "save_unmarked_present"
        reason = (request.POST.get("change_reason") or "")[:200]
        roster = list(students_for_session(session))
        roster_ids = {student.pk for student in roster}
        orphans = list(orphan_entries_for_session(session, roster_ids))
        allowed_ids = roster_ids | {entry.student_id for entry in orphans}
        entries = {
            entry.student_id: entry
            for entry in AttendanceEntry.objects.filter(activity_session=session)
            .select_related("student")
        }
        valid_statuses = {choice[0] for choice in AttendanceStatus.choices}
        errors = []
        to_save = []

        for student_id in allowed_ids:
            posted_status = (request.POST.get(f"status_{student_id}") or "").strip()
            posted_notes = (request.POST.get(f"notes_{student_id}") or "")[:200]
            entry = entries.get(student_id)

            if save_unmarked:
                if entry is not None:
                    continue
                if not can_take_attendance(request.user, session):
                    raise PermissionDenied
                to_save.append(
                    AttendanceEntry(
                        activity_session=session,
                        student_id=student_id,
                        status=AttendanceStatus.PRESENT,
                        notes=posted_notes,
                        taken_by=request.user,
                        taken_at=timezone.now(),
                    )
                )
                continue

            if entry is None:
                if not posted_status:
                    continue
                if posted_status not in valid_statuses:
                    errors.append(f"Invalid status for student {student_id}.")
                    continue
                if not can_take_attendance(request.user, session):
                    raise PermissionDenied
                to_save.append(
                    AttendanceEntry(
                        activity_session=session,
                        student_id=student_id,
                        status=posted_status,
                        notes=posted_notes,
                        taken_by=request.user,
                        taken_at=timezone.now(),
                    )
                )
                continue

            if not posted_status:
                errors.append(
                    f"{entry.student}: an already saved attendance mark cannot be "
                    "cleared here. Only Administration can delete an attendance entry."
                )
                continue
            if posted_status not in valid_statuses:
                errors.append(f"{entry.student}: invalid status.")
                continue

            stored_taken_by = entry.taken_by
            stored_taken_at = entry.taken_at
            if posted_status == entry.status:
                if posted_notes != entry.notes:
                    if not can_take_attendance(request.user, session):
                        raise PermissionDenied
                    entry.notes = posted_notes
                    entry.taken_by = stored_taken_by
                    entry.taken_at = stored_taken_at
                    to_save.append(entry)
                continue

            if not can_change_attendance_status(request.user, session):
                raise PermissionDenied
            entry.status = posted_status
            entry.notes = posted_notes
            entry.taken_by = stored_taken_by
            entry.taken_at = stored_taken_at
            entry.updated_by = request.user
            entry._status_change_reason = reason
            to_save.append(entry)

        if errors:
            return errors

        try:
            with transaction.atomic():
                for obj in to_save:
                    obj.save()
        except IntegrityError:
            return [
                "Another user saved attendance for this session at the same time. "
                "Please review the current marks and try again."
            ]
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                messages_out = []
                for field, field_errors in exc.message_dict.items():
                    for item in field_errors:
                        messages_out.append(f"{field}: {item}")
                return messages_out or [str(exc)]
            if hasattr(exc, "messages"):
                return list(exc.messages)
            return [str(exc)]
        return []

    def mark_attendance_view(self, request, object_id):
        from .attendance_auth import can_take_attendance

        session = (
            ActivitySession.objects.select_related(
                "activity_type",
                "class_section",
                "house",
                "student_group",
                "responsible_staff",
                "routine_slot",
                "academic_year",
            )
            .filter(pk=object_id)
            .first()
        )
        if session is None:
            raise Http404("Activity session not found.")

        if not session.activity_type_id or not session.activity_type.takes_attendance:
            return self._forbidden_attendance(
                request,
                "This activity does not take attendance.",
            )
        if not can_take_attendance(request.user, session):
            return self._forbidden_attendance(
                request,
                "You are not authorized to mark attendance for this session.",
            )

        errors = []
        posted = None
        if request.method == "POST":
            errors = self._apply_mark_attendance(request, session)
            if not errors:
                self.message_user(request, "Attendance saved.", messages.SUCCESS)
                return HttpResponseRedirect(request.path)
            posted = request.POST

        context = self._mark_attendance_context(request, session, errors=errors, posted=posted)
        return TemplateResponse(
            request,
            "admin/school/activitysession/mark_attendance.html",
            context,
        )


@admin.register(ActivitySessionParticipant)
class ActivitySessionParticipantAdmin(admin.ModelAdmin):
    list_display = ("session", "student")
    search_fields = (
        "student__admission_number",
        "student__first_name",
        "student__last_name",
        "session__name",
    )
    autocomplete_fields = ("session", "student")
    list_select_related = ("session", "student")

    def has_add_permission(self, request):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(StaffDutyAssignment)
class StaffDutyAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "duty_type", "staff", "academic_year")
    list_filter = ("academic_year", "duty_type", "duty_type__unique_per_day", "date")
    date_hierarchy = "date"
    search_fields = (
        "staff__username",
        "staff__first_name",
        "staff__last_name",
        "duty_type__name",
    )
    autocomplete_fields = ("duty_type", "staff", "academic_year")
    list_select_related = ("duty_type", "staff", "academic_year")
    ordering = ("-date", "duty_type")
    list_per_page = 50


class AttendanceEntryAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "activity_session",
        "status",
        "taken_by",
        "taken_at",
        "updated_by",
        "updated_at",
    )
    list_filter = ("status", "activity_session__date", "activity_session__activity_type")
    search_fields = (
        "student__admission_number",
        "student__first_name",
        "student__last_name",
        "activity_session__name",
    )
    autocomplete_fields = ("student", "activity_session", "taken_by", "updated_by")
    list_select_related = (
        "student",
        "activity_session__activity_type",
        "activity_session__responsible_staff",
        "taken_by",
        "updated_by",
    )
    inlines = (AttendanceRevisionInline,)
    list_per_page = 50
    readonly_fields = (
        "activity_session",
        "student",
        "status",
        "taken_by",
        "taken_at",
        "updated_by",
        "updated_at",
        "notes",
    )

    def get_queryset(self, request):
        from .attendance_auth import sessions_user_may_mark

        qs = super().get_queryset(request)
        return qs.filter(activity_session__in=sessions_user_may_mark(request.user))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        if obj is None:
            return True
        from .attendance_auth import can_change_attendance_status

        return can_change_attendance_status(request.user, obj.activity_session)


admin.site.register(AttendanceEntry, AttendanceEntryAdmin)


@admin.register(AttendanceRevision)
class AttendanceRevisionAdmin(admin.ModelAdmin):
    list_display = ("entry", "old_status", "new_status", "changed_by", "changed_at", "reason")
    list_filter = ("old_status", "new_status", "changed_at")
    search_fields = (
        "entry__student__admission_number",
        "entry__student__first_name",
        "entry__student__last_name",
        "reason",
    )
    autocomplete_fields = ("entry", "changed_by")
    list_select_related = ("entry", "changed_by", "entry__student")
    readonly_fields = ("entry", "old_status", "new_status", "changed_by", "changed_at", "reason")
    ordering = ("-changed_at",)

    def get_queryset(self, request):
        from .attendance_auth import sessions_user_may_mark

        qs = super().get_queryset(request)
        return qs.filter(entry__activity_session__in=sessions_user_may_mark(request.user))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VidyalayaProfile)
class VidyalayaProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "district", "state", "nvs_region", "motto")
    fields = (
        "name",
        "campus",
        "district",
        "state",
        "nvs_region",
        "udise_code",
        "established_year",
        "motto",
        "about",
    )

    def has_add_permission(self, request):
        return not VidyalayaProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Circular)
class CircularAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "published_on", "is_published")
    list_filter = ("audience", "is_published", "published_on")
    search_fields = ("title", "body")
    autocomplete_fields = ("created_by",)
    ordering = ("-published_on", "-pk")


@admin.register(MessMenu)
class MessMenuAdmin(admin.ModelAdmin):
    list_display = ("date", "breakfast", "lunch", "evening_snacks", "dinner")
    list_filter = ("date",)
    search_fields = ("breakfast", "lunch", "dinner", "note")
    ordering = ("-date",)


@admin.register(OutingPass)
class OutingPassAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "student",
        "purpose",
        "destination",
        "status",
        "issued_by",
    )
    list_filter = ("date", "status")
    search_fields = (
        "student__admission_number",
        "student__first_name",
        "student__last_name",
        "purpose",
        "destination",
    )
    autocomplete_fields = ("student", "issued_by")
    list_select_related = ("student", "issued_by")
    ordering = ("-date", "-departure_time")


@admin.register(StudentOffice)
class StudentOfficeAdmin(admin.ModelAdmin):
    list_display = ("role", "student", "house", "academic_year")
    list_filter = ("academic_year", "role", "house")
    search_fields = ("student__first_name", "student__last_name", "student__admission_number")
    autocomplete_fields = ("student", "academic_year", "house")


@admin.register(HouseCompetition)
class HouseCompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "held_on", "academic_year")
    list_filter = ("academic_year", "kind")
    search_fields = ("name", "venue")


@admin.register(HouseCompetitionResult)
class HouseCompetitionResultAdmin(admin.ModelAdmin):
    list_display = ("competition", "house", "position", "points")
    list_filter = ("house", "competition__academic_year")


@admin.register(SickBayVisit)
class SickBayVisitAdmin(admin.ModelAdmin):
    list_display = ("visited_on", "student", "complaint", "referred_out")
    list_filter = ("visited_on", "referred_out")
    search_fields = ("student__first_name", "student__last_name", "complaint")
    autocomplete_fields = ("student", "recorded_by")


@admin.register(VisitorPass)
class VisitorPassAdmin(admin.ModelAdmin):
    list_display = ("visited_on", "visitor_name", "student", "purpose")
    list_filter = ("visited_on",)
    search_fields = ("visitor_name", "student__first_name", "student__last_name")
    autocomplete_fields = ("student",)


@admin.register(LibraryBook)
class LibraryBookAdmin(admin.ModelAdmin):
    list_display = ("accession_no", "title", "author", "subject")
    search_fields = ("accession_no", "title", "author")


@admin.register(LibraryIssue)
class LibraryIssueAdmin(admin.ModelAdmin):
    list_display = ("book", "student", "issued_on", "due_on", "returned_on")
    list_filter = ("issued_on",)
    autocomplete_fields = ("book", "student")


@admin.register(ExamTerm)
class ExamTermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "starts_on", "ends_on")
    list_filter = ("academic_year",)
    search_fields = ("name",)


@admin.register(AssessmentMark)
class AssessmentMarkAdmin(admin.ModelAdmin):
    list_display = ("term", "student", "subject", "marks_obtained", "max_marks")
    list_filter = ("term", "subject")
    autocomplete_fields = ("term", "student", "subject")


@admin.register(CommitteeSeat)
class CommitteeSeatAdmin(admin.ModelAdmin):
    list_display = ("kind", "role", "member_name", "academic_year")
    list_filter = ("kind", "academic_year")


@admin.register(VidyalayaEvent)
class VidyalayaEventAdmin(admin.ModelAdmin):
    list_display = ("held_on", "title", "kind", "venue", "academic_year")
    list_filter = ("kind", "academic_year")
    search_fields = ("title", "venue")


@admin.register(VvnEntry)
class VvnEntryAdmin(admin.ModelAdmin):
    list_display = ("student", "academic_year", "amount", "is_paid")
    list_filter = ("academic_year", "is_paid")
    autocomplete_fields = ("student", "academic_year")


@admin.register(MigrationRecord)
class MigrationRecordAdmin(admin.ModelAdmin):
    list_display = ("direction", "student", "other_jnv", "stream", "academic_year")
    list_filter = ("direction", "academic_year")
    search_fields = ("student__first_name", "student__last_name", "other_jnv")
    autocomplete_fields = ("student", "academic_year")
