from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

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
    list_display = ("display_name", "grade_name", "section_name", "is_active")
    list_filter = ("is_active", "grade_name")
    search_fields = ("display_name", "grade_name", "section_name")
    ordering = ("grade_name", "section_name")


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
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name",)


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


class AttendanceRevisionInline(admin.TabularInline):
    model = AttendanceRevision
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "changed_by", "changed_at", "reason")
    fields = ("old_status", "new_status", "changed_by", "changed_at", "reason")

    def has_add_permission(self, request, obj=None):
        return False


class AttendanceEntryAdminForm(forms.ModelForm):
    change_reason = forms.CharField(
        required=False,
        help_text="Optional reason stored on the revision if status changes.",
    )

    class Meta:
        model = AttendanceEntry
        fields = "__all__"


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
    list_display = ("date", "academic_year", "routine", "note")
    list_filter = ("academic_year", "routine")
    date_hierarchy = "date"
    list_editable = ("routine", "note")
    search_fields = ("note", "routine__name")
    autocomplete_fields = ("academic_year",)
    ordering = ("-date",)
    list_select_related = ("academic_year", "routine")
    actions = ("generate_daily_sessions",)

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
    )
    list_filter = ("academic_year", "activity_type", "audience_kind", "date")
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
    form = AttendanceEntryAdminForm
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
    autocomplete_fields = ("student", "taken_by", "updated_by")
    list_select_related = (
        "student",
        "activity_session__activity_type",
        "activity_session__responsible_staff",
        "taken_by",
        "updated_by",
    )
    inlines = (AttendanceRevisionInline,)
    list_per_page = 50

    def get_exclude(self, request, obj=None):
        if obj is None:
            return ("taken_by", "taken_at")
        return ()

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("taken_by", "taken_at")
        return ()

    def get_queryset(self, request):
        from .attendance_auth import sessions_user_may_mark

        qs = super().get_queryset(request)
        return qs.filter(activity_session__in=sessions_user_may_mark(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        from .attendance_auth import sessions_user_may_mark

        if db_field.name == "activity_session":
            kwargs["queryset"] = sessions_user_may_mark(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None:
            return True
        from .attendance_auth import can_change_attendance_status

        return can_change_attendance_status(request.user, obj.activity_session)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if request.user.category != UserCategory.ADMINISTRATION:
            return False
        if obj is None:
            return True
        from .attendance_auth import can_change_attendance_status

        return can_change_attendance_status(request.user, obj.activity_session)

    def save_model(self, request, obj, form, change):
        from .attendance_auth import can_change_attendance_status, can_take_attendance

        session = obj.activity_session
        if not change:
            if not can_take_attendance(request.user, session):
                raise PermissionDenied
            obj.taken_by = request.user
        else:
            stored = AttendanceEntry.objects.get(pk=obj.pk)
            obj.taken_by = stored.taken_by
            obj.taken_at = stored.taken_at
            if stored.status != obj.status:
                if not can_change_attendance_status(request.user, session):
                    raise PermissionDenied
                obj._status_change_reason = form.cleaned_data.get("change_reason", "")
                obj.updated_by = request.user
            elif not can_take_attendance(request.user, session):
                raise PermissionDenied
        super().save_model(request, obj, form, change)


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
    readonly_fields = ("entry", "old_status", "new_status", "changed_by", "changed_at")
    ordering = ("-changed_at",)

    def get_queryset(self, request):
        from .attendance_auth import sessions_user_may_mark

        qs = super().get_queryset(request)
        return qs.filter(entry__activity_session__in=sessions_user_may_mark(request.user))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
