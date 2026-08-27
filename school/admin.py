from django.contrib import admin

from .models import (
    AcademicYear,
    ClassSection,
    ParentProfile,
    Student,
    Subject,
    TeacherProfile,
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
