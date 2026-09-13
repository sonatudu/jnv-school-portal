from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from accounts.models import UserCategory

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

    def get_urls(self):
        extra = [
            path(
                "<id>/profile/",
                self.admin_site.admin_view(self.profile_view),
                name="accounts_user_profile",
            ),
        ]
        return extra + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and request.GET.get("edit") != "1":
            return HttpResponseRedirect(
                reverse("admin:accounts_user_profile", args=[object_id])
            )
        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context=extra_context,
        )

    def profile_view(self, request, id):
        from school.staff_profile import build_user_profile

        user = (
            User.objects.select_related("designation", "designation__group")
            .filter(pk=id)
            .first()
        )
        if user is None:
            raise Http404("User not found.")
        if (
            request.user.category == UserCategory.PARENT
            and request.user.pk != user.pk
        ):
            raise Http404("User not found.")

        extra_rows = user.extra_biodata_rows.all()
        from school.biodata_extra import handle_extra_biodata_post

        if handle_extra_biodata_post(request, extra_rows, {"user": user}):
            return HttpResponseRedirect(request.get_full_path())

        profile = build_user_profile(user)
        context = {
            **self.admin_site.each_context(request),
            "title": user.get_full_name() or user.username,
            "opts": self.model._meta,
            "profile_user": user,
            "profile": profile,
            "extra_rows": extra_rows,
            "edit_url": reverse("admin:accounts_user_change", args=[user.pk])
            + "?edit=1",
            "can_edit": request.user.category == UserCategory.ADMINISTRATION,
        }
        return TemplateResponse(
            request,
            "admin/accounts/user/profile.html",
            context,
        )
