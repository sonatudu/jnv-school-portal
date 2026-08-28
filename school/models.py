from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import UserCategory


class Gender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"
    OTHER = "other", "Other"


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True, help_text='Example: "2026-27"')
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="academic_year_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["is_current"],
                condition=models.Q(is_current=True),
                name="unique_current_academic_year",
            ),
        ]

    def __str__(self):
        return self.name


class ClassSection(models.Model):
    """A teaching group such as VI-A or XI-Science.

    Grade and section/stream values are stored in the database so they can be
    managed in admin without hard-coded class lists in Python.
    """

    grade_name = models.CharField(
        max_length=20,
        help_text="Class/grade label stored in the database, e.g. VI or XI.",
    )
    section_name = models.CharField(
        max_length=30,
        help_text="Section or stream stored in the database, e.g. A or Science.",
    )
    display_name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Readable name, e.g. VI-A or XI-Science.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["grade_name", "section_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["grade_name", "section_name"],
                name="unique_grade_section",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = f"{self.grade_name}-{self.section_name}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name


class Subject(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], name="unique_subject_name"),
        ]

    def __str__(self):
        return self.name if not self.code else f"{self.name} ({self.code})"


class Student(models.Model):
    admission_number = models.CharField(max_length=30, unique=True)
    roll_number = models.PositiveIntegerField()
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=16, choices=Gender.choices)
    is_active = models.BooleanField(default=True)
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="students",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="students",
    )

    class Meta:
        ordering = ["class_section", "roll_number", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "class_section", "roll_number"],
                name="unique_roll_in_section_year",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "class_section"]),
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["is_active"]),
        ]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)

    def __str__(self):
        return f"{self.full_name} ({self.admission_number})"


class TeacherProfile(models.Model):
    """Teacher-specific record. Login stays on accounts.User (Staff category)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
        limit_choices_to={"category": UserCategory.STAFF},
    )

    class Meta:
        verbose_name = "teacher profile"
        verbose_name_plural = "teacher profiles"

    def clean(self):
        super().clean()
        if self.user_id and self.user.category != UserCategory.STAFF:
            raise ValidationError(
                {"user": "Teacher profiles can only be linked to Staff users."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.user)


class ParentProfile(models.Model):
    """Parent-specific record. Login stays on accounts.User (Parent category)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parent_profile",
        limit_choices_to={"category": UserCategory.PARENT},
    )

    class Meta:
        verbose_name = "parent profile"
        verbose_name_plural = "parent profiles"

    def clean(self):
        super().clean()
        if self.user_id and self.user.category != UserCategory.PARENT:
            raise ValidationError(
                {"user": "Parent profiles can only be linked to Parent users."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.user)


class House(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=~models.Q(code=""),
                name="unique_house_code_when_set",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name if not self.code else f"{self.name} ({self.code})"


class StudentHouseMembership(models.Model):
    """A student's house for one academic year. History is kept across years."""

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="house_memberships",
    )
    house = models.ForeignKey(
        House,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="house_memberships",
    )

    class Meta:
        ordering = ["-academic_year", "house", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                name="unique_student_house_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "house"]),
        ]
        verbose_name = "student house membership"
        verbose_name_plural = "student house memberships"

    def __str__(self):
        return f"{self.student} — {self.house} ({self.academic_year})"


class TeachingAssignment(models.Model):
    """Teacher teaches a subject to a class section in an academic year."""

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
    )

    class Meta:
        ordering = ["-academic_year", "class_section", "subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "subject", "class_section", "academic_year"],
                name="unique_teaching_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "class_section"]),
            models.Index(fields=["academic_year", "teacher"]),
            models.Index(fields=["subject"]),
        ]

    def clean(self):
        super().clean()
        if self.teacher_id and self.teacher.user.category != UserCategory.STAFF:
            raise ValidationError(
                {"teacher": "Teaching assignments require a Staff teacher profile."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.teacher} — {self.subject} — {self.class_section} "
            f"({self.academic_year})"
        )


class ClassTeacherAssignment(models.Model):
    """One class teacher per class section per academic year."""

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="class_teacher_assignments",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="class_teacher_assignments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="class_teacher_assignments",
    )

    class Meta:
        ordering = ["-academic_year", "class_section"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_section", "academic_year"],
                name="unique_class_teacher_per_section_year",
            ),
            models.UniqueConstraint(
                fields=["teacher", "academic_year"],
                name="unique_class_teacher_per_teacher_year",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "teacher"]),
        ]

    def clean(self):
        super().clean()
        if self.teacher_id and self.teacher.user.category != UserCategory.STAFF:
            raise ValidationError(
                {"teacher": "Class teacher assignments require a Staff teacher profile."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.teacher} — Class Teacher, {self.class_section} ({self.academic_year})"


class HouseMasterAssignment(models.Model):
    """Staff responsible for a house in an academic year.

    Multiple staff may be assigned to the same house (e.g. House Master and
    House Mistress). Role comes from the staff user's existing designation,
    not a second role field.
    """

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="house_master_assignments",
        limit_choices_to={"category": UserCategory.STAFF},
    )
    house = models.ForeignKey(
        House,
        on_delete=models.PROTECT,
        related_name="master_assignments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="house_master_assignments",
    )

    class Meta:
        ordering = ["-academic_year", "house"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "house", "academic_year"],
                name="unique_house_master_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "house"]),
            models.Index(fields=["academic_year", "staff"]),
        ]

    def clean(self):
        super().clean()
        if self.staff_id and self.staff.category != UserCategory.STAFF:
            raise ValidationError(
                {"staff": "House master assignments require a Staff user."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.staff} — {self.house} ({self.academic_year})"
