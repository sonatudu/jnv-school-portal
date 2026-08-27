from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Designation, User


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "group")
    list_filter = ("category",)
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "category",
        "designation",
        "is_staff",
    )
    search_fields = ("username", "first_name", "last_name", "email")
    list_filter = ("category", "is_staff", "is_superuser", "is_active", "groups")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("School role", {"fields": ("category", "designation")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("School role", {"fields": ("category", "designation")}),
    )
