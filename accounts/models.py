from django.contrib.auth.models import AbstractUser, Group, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models


class UserCategory(models.TextChoices):
    ADMINISTRATION = "administration", "Administration"
    STAFF = "staff", "Staff"
    PARENT = "parent", "Parent"


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("category", UserCategory.ADMINISTRATION)
        return super().create_superuser(username, email, password, **extra_fields)


class Designation(models.Model):
    """Job title within a user category (e.g. Principal, Teacher).

    Not a permission. Optionally linked to a Django Group so that assigning
    this designation can grant the group's permissions.
    """

    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=32, choices=UserCategory.choices)
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="designations",
        help_text="Optional Django Group used to grant permissions for this designation.",
    )

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class User(AbstractUser):
    """Portal user. Teachers are Staff; there is no separate Teacher category."""

    category = models.CharField(max_length=32, choices=UserCategory.choices)
    designation = models.ForeignKey(
        Designation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text="Optional job title. Teachers use a Staff designation, not a separate category.",
    )

    objects = UserManager()

    class Meta:
        ordering = ["username"]

    def clean(self):
        super().clean()
        if self.designation and self.category and self.designation.category != self.category:
            raise ValidationError(
                {
                    "designation": (
                        "Designation must belong to the same category as the user "
                        f"({self.get_category_display()})."
                    )
                }
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.designation_id and self.designation.group_id:
            self.groups.add(self.designation.group)


class UserBiodataRow(models.Model):
    """Admin-added extra biodata field for one user."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="extra_biodata_rows",
    )
    label = models.CharField(max_length=120)
    value = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "label"],
                name="unique_user_biodata_label",
            ),
        ]
        verbose_name = "user biodata row"
        verbose_name_plural = "user biodata rows"

    def __str__(self):
        return f"{self.user}: {self.label}"
