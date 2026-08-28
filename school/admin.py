from django.contrib import admin

from .models import (
    AcademicYear,
    ClassSection,
    ClassTeacherAssignment,
    House,
    HouseMasterAssignment,
    ParentProfile,
    Student,
    StudentHouseMembership,
    Subject,
    TeacherProfile,
    TeachingAssignment,
)


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
