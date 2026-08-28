from datetime import date

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
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
    ClassTimetableEntry,
    DutyType,
    House,
    HouseMasterAssignment,
    ParentProfile,
    Routine,
    RoutineSlot,
    SchoolCalendarDay,
    StaffDutyAssignment,
    Student,
    StudentGroup,
    StudentGroupMembership,
    StudentHouseMembership,
    Subject,
    TeacherProfile,
    TeachingAssignment,
    AttendanceStatus,
)
from accounts.models import UserCategory


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_current")
    list_filter = ("is_current",)
    search_fields = ("name",)
    ordering = ("-start_date",)


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "grade_name",
        "section_name",
        "is_active",
        "attendance_report_link",
    )
    list_filter = ("is_active", "grade_name")
    search_fields = ("display_name", "grade_name", "section_name")
    ordering = ("grade_name", "section_name")

    def get_urls(self):
        extra = [
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
            raise Http404("Class section not found.")

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


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "admission_number",
        "roll_number",
        "full_name",
        "class_section",
        "academic_year",
        "gender",
        "is_active",
        "attendance_history_link",
    )
    list_filter = ("is_active", "academic_year", "class_section", "gender")
    search_fields = (
        "admission_number",
        "first_name",
        "middle_name",
        "last_name",
        "roll_number",
    )
    autocomplete_fields = ("class_section", "academic_year")
    list_select_related = ("class_section", "academic_year")
    list_per_page = 50

    def changelist_view(self, request, extra_context=None):
        self._request = request
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
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
                row["entry_url"] = reverse(
                    "admin:school_attendanceentry_change",
                    args=[row["entry"].pk],
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


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "user_is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)

    @admin.display(description="Active", boolean=True)
    def user_is_active(self, obj):
        return obj.user.is_active


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "attendance_report_link")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name",)

    def get_urls(self):
        extra = [
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
        "house__code",
    )
    autocomplete_fields = ("student", "house", "academic_year")
    list_select_related = ("student", "house", "academic_year")
    ordering = ("-academic_year", "house", "student")
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
    list_display = ("staff", "staff_designation", "house", "academic_year")
    list_filter = ("academic_year", "house")
    search_fields = (
        "staff__username",
        "staff__first_name",
        "staff__last_name",
        "house__name",
        "house__code",
    )
    autocomplete_fields = ("staff", "house", "academic_year")
    list_select_related = ("staff", "staff__designation", "house", "academic_year")
    ordering = ("-academic_year", "house")

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
    fields = ("sort_order", "activity_type", "name", "start_time", "end_time")

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
    list_display = ("name", "routine", "activity_type", "start_time", "end_time", "sort_order")
    list_filter = ("routine__academic_year", "routine", "activity_type")
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

    def changelist_view(self, request, extra_context=None):
        self._request = request
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        extra = [
            path(
                "attendance-overview/",
                self.admin_site.admin_view(self.attendance_overview_view),
                name="school_activitysession_attendance_overview",
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
        }
        return TemplateResponse(
            request,
            "admin/school/activitysession/attendance_overview.html",
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

    def _attendance_row(self, request, student, entry, posted_status, posted_notes):
        latest = None
        entry_url = ""
        if entry is not None:
            revisions = list(entry.revisions.all())
            latest = revisions[0] if revisions else None
            entry_url = reverse(
                "admin:school_attendanceentry_change",
                args=[entry.pk],
            )
        if posted_status is None:
            posted_status = entry.status if entry is not None else ""
        if posted_notes is None:
            posted_notes = entry.notes if entry is not None else ""
        return {
            "student": student,
            "entry": entry,
            "latest_revision": latest,
            "entry_admin_url": entry_url,
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
    list_filter = ("academic_year", "duty_type", "date")
    search_fields = (
        "staff__username",
        "staff__first_name",
        "staff__last_name",
        "duty_type__name",
    )
    autocomplete_fields = ("duty_type", "staff", "academic_year")
    list_select_related = ("duty_type", "staff", "academic_year")
    ordering = ("-date",)


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
