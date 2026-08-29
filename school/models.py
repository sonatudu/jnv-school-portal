from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_class_membership()

    def _sync_class_membership(self):
        if not self.pk or not self.class_section_id or not self.academic_year_id:
            return
        StudentClassMembership.objects.update_or_create(
            student_id=self.pk,
            academic_year_id=self.academic_year_id,
            defaults={"class_section_id": self.class_section_id},
        )

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


class StudentClassMembership(models.Model):
    """A student's class section for one academic year. History is kept across years."""

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="class_memberships",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="class_memberships",
    )

    class Meta:
        ordering = ["-academic_year", "class_section", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                name="unique_student_class_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "class_section"]),
        ]
        verbose_name = "student class membership"
        verbose_name_plural = "student class memberships"

    def __str__(self):
        return f"{self.student} — {self.class_section} ({self.academic_year})"


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


class AudienceKind(models.TextChoices):
    CLASS = "class", "Class section"
    HOUSE = "house", "House"
    SCHOOL = "school", "Whole school"
    STUDENT_GROUP = "student_group", "Named student group"
    SELECTED_STUDENTS = "selected_students", "Selected students"


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    LEAVE = "leave", "Leave"


_ATTENDANCE_ACTOR_CATEGORIES = (UserCategory.STAFF, UserCategory.ADMINISTRATION)
_ATTENDANCE_ACTOR_LIMIT = {"category__in": list(_ATTENDANCE_ACTOR_CATEGORIES)}


class ActivityType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=20, blank=True)
    default_audience_kind = models.CharField(
        max_length=32,
        choices=AudienceKind.choices,
        blank=True,
        help_text="Optional hint only. Sessions may use a different audience kind.",
    )
    requires_subject = models.BooleanField(default=False)
    takes_attendance = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def is_class_timetable_eligible(self):
        return self.is_active and self.default_audience_kind == AudienceKind.CLASS


class DutyType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=20, blank=True)
    unique_per_day = models.BooleanField(
        default=False,
        help_text="If set, only one staff member may hold this duty on a given date (e.g. MOD).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Routine(models.Model):
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="routines",
    )
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["academic_year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "name"],
                name="unique_routine_name_per_year",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"


class RoutineSlot(models.Model):
    routine = models.ForeignKey(
        Routine,
        on_delete=models.PROTECT,
        related_name="slots",
    )
    activity_type = models.ForeignKey(
        ActivityType,
        on_delete=models.PROTECT,
        related_name="routine_slots",
    )
    name = models.CharField(max_length=80, help_text='Example: "Period 1" or "Remedial"')
    start_time = models.TimeField()
    end_time = models.TimeField()
    sort_order = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["routine", "sort_order", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["routine", "sort_order"],
                name="unique_slot_order_per_routine",
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="routine_slot_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["routine", "start_time"]),
        ]

    def __str__(self):
        return f"{self.routine}: {self.name}"


class SchoolCalendarDay(models.Model):
    date = models.DateField(unique=True)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="calendar_days",
    )
    routine = models.ForeignKey(
        Routine,
        on_delete=models.PROTECT,
        related_name="calendar_days",
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["academic_year", "date"]),
        ]

    def clean(self):
        super().clean()
        if self.academic_year_id and self.date:
            year = self.academic_year
            if self.date < year.start_date or self.date > year.end_date:
                raise ValidationError(
                    {"date": "Date must fall within the selected academic year."}
                )
        if (
            self.routine_id
            and self.academic_year_id
            and self.routine.academic_year_id != self.academic_year_id
        ):
            raise ValidationError(
                {"routine": "Routine must belong to the same academic year."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} — {self.routine}"


class ClassTimetableEntry(models.Model):
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="timetable_entries",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="timetable_entries",
    )
    routine_slot = models.ForeignKey(
        RoutineSlot,
        on_delete=models.PROTECT,
        related_name="timetable_entries",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="timetable_entries",
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="timetable_entries",
    )

    class Meta:
        ordering = ["academic_year", "class_section", "routine_slot"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_section", "routine_slot"],
                name="unique_timetable_entry_per_class_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "class_section"]),
            models.Index(fields=["teacher"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.routine_slot_id:
            slot = self.routine_slot
            if (
                self.academic_year_id
                and slot.routine.academic_year_id != self.academic_year_id
            ):
                errors["routine_slot"] = "Routine slot must belong to the same academic year."
            elif not slot.routine.is_active:
                errors["routine_slot"] = "Cannot assign an inactive routine."
            elif not slot.activity_type.is_class_timetable_eligible():
                errors["routine_slot"] = (
                    "This slot's activity type is not eligible for a class timetable entry."
                )
        if self.teacher_id and self.teacher.user.category != UserCategory.STAFF:
            errors["teacher"] = "Timetable entries require a Staff teacher profile."
        elif self.teacher_id and not self.teacher.user.is_active:
            errors["teacher"] = "Cannot assign an inactive teacher."
        if self.class_section_id and not self.class_section.is_active:
            errors["class_section"] = "Cannot assign an inactive class section."
        if self.subject_id and not self.subject.is_active:
            errors["subject"] = "Cannot assign an inactive subject."
        if (
            "teacher" not in errors
            and self.teacher_id
            and self.subject_id
            and self.class_section_id
            and self.academic_year_id
            and not TeachingAssignment.objects.filter(
                teacher=self.teacher,
                subject=self.subject,
                class_section=self.class_section,
                academic_year=self.academic_year,
            ).exists()
        ):
            errors["teacher"] = (
                "This teacher is not assigned to teach that subject to this class "
                "in this academic year."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.class_section} — {self.routine_slot.name} — {self.subject} "
            f"({self.academic_year})"
        )


class StudentGroup(models.Model):
    """Named custom group (e.g. a recurring remedial set), not a class or house."""

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="student_groups",
    )
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["academic_year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "name"],
                name="unique_student_group_per_year",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"


class StudentGroupMembership(models.Model):
    group = models.ForeignKey(
        StudentGroup,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="group_memberships",
    )

    class Meta:
        ordering = ["group", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "student"],
                name="unique_student_in_group",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.group}"


class ActivitySession(models.Model):
    """A date-specific occurrence of an activity for one target group.

    Audience is not limited to class/house/school: named groups and selected
    students are supported, and further kinds can be added later.
    Staff and times are snapshotted so later assignment changes do not rewrite history.
    """

    date = models.DateField()
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
    )
    routine_slot = models.ForeignKey(
        RoutineSlot,
        on_delete=models.PROTECT,
        related_name="sessions",
        null=True,
        blank=True,
    )
    activity_type = models.ForeignKey(
        ActivityType,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    name = models.CharField(max_length=80)
    start_time = models.TimeField()
    end_time = models.TimeField()
    audience_kind = models.CharField(max_length=32, choices=AudienceKind.choices)
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
        null=True,
        blank=True,
    )
    house = models.ForeignKey(
        House,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
        null=True,
        blank=True,
    )
    student_group = models.ForeignKey(
        StudentGroup,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
        null=True,
        blank=True,
    )
    responsible_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="responsible_activity_sessions",
        limit_choices_to={"category": UserCategory.STAFF},
    )
    teaching_assignment = models.ForeignKey(
        TeachingAssignment,
        on_delete=models.PROTECT,
        related_name="activity_sessions",
        null=True,
        blank=True,
        help_text="Optional hint only. Historical staff is responsible_staff.",
    )

    class Meta:
        ordering = ["-date", "start_time", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="activity_session_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        class_section__isnull=False,
                        house__isnull=True,
                        student_group__isnull=True,
                    )
                    | models.Q(
                        class_section__isnull=True,
                        house__isnull=False,
                        student_group__isnull=True,
                    )
                    | models.Q(
                        class_section__isnull=True,
                        house__isnull=True,
                        student_group__isnull=False,
                    )
                    | models.Q(
                        class_section__isnull=True,
                        house__isnull=True,
                        student_group__isnull=True,
                    )
                ),
                name="activity_session_at_most_one_target",
            ),
            models.UniqueConstraint(
                fields=["date", "routine_slot", "class_section"],
                condition=models.Q(class_section__isnull=False, routine_slot__isnull=False),
                name="unique_class_session_per_slot_date",
            ),
            models.UniqueConstraint(
                fields=["date", "routine_slot", "house"],
                condition=models.Q(house__isnull=False, routine_slot__isnull=False),
                name="unique_house_session_per_slot_date",
            ),
            models.UniqueConstraint(
                fields=["date", "routine_slot", "student_group"],
                condition=models.Q(student_group__isnull=False, routine_slot__isnull=False),
                name="unique_group_session_per_slot_date",
            ),
            models.UniqueConstraint(
                fields=["date", "routine_slot"],
                condition=models.Q(
                    audience_kind="school",
                    routine_slot__isnull=False,
                ),
                name="unique_school_session_per_slot_date",
            ),
            models.UniqueConstraint(
                fields=["date", "routine_slot", "name"],
                condition=models.Q(audience_kind="selected_students"),
                name="unique_selected_session_per_slot_date_name",
            ),
        ]
        indexes = [
            models.Index(fields=["date", "start_time"]),
            models.Index(fields=["academic_year", "date"]),
            models.Index(fields=["audience_kind"]),
            models.Index(fields=["responsible_staff", "date"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        kind = self.audience_kind
        if kind == AudienceKind.CLASS:
            if not self.class_section_id:
                errors["class_section"] = "Class section is required for class sessions."
            if self.house_id or self.student_group_id:
                errors["audience_kind"] = (
                    "Class sessions cannot also target a house or student group."
                )
        elif kind == AudienceKind.HOUSE:
            if not self.house_id:
                errors["house"] = "House is required for house sessions."
            if self.class_section_id or self.student_group_id:
                errors["audience_kind"] = (
                    "House sessions cannot also target a class or student group."
                )
        elif kind == AudienceKind.SCHOOL:
            if self.class_section_id or self.house_id or self.student_group_id:
                errors["audience_kind"] = (
                    "Whole-school sessions cannot target a class, house, or group."
                )
        elif kind == AudienceKind.STUDENT_GROUP:
            if not self.student_group_id:
                errors["student_group"] = "Student group is required for group sessions."
            if self.class_section_id or self.house_id:
                errors["audience_kind"] = (
                    "Student-group sessions cannot also target a class or house."
                )
        elif kind == AudienceKind.SELECTED_STUDENTS:
            if self.class_section_id or self.house_id or self.student_group_id:
                errors["audience_kind"] = (
                    "Selected-student sessions should use participants, "
                    "not class/house/group fields."
                )
        if self.activity_type_id and self.activity_type.requires_subject and not self.subject_id:
            errors["subject"] = "This activity type requires a subject."
        if self.responsible_staff_id and self.responsible_staff.category != UserCategory.STAFF:
            errors["responsible_staff"] = "Responsible staff must be a Staff user."
        if (
            self.routine_slot_id
            and self.academic_year_id
            and self.routine_slot.routine.academic_year_id != self.academic_year_id
        ):
            errors["routine_slot"] = "Routine slot must belong to the same academic year."
        if (
            self.student_group_id
            and self.academic_year_id
            and self.student_group.academic_year_id != self.academic_year_id
        ):
            errors["student_group"] = "Student group must belong to the same academic year."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} {self.name}"


class ActivitySessionParticipant(models.Model):
    """Explicit student list for a session (selected students / custom subsets)."""

    session = models.ForeignKey(
        ActivitySession,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="session_participations",
    )

    class Meta:
        ordering = ["session", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"],
                name="unique_participant_per_session",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.session}"


class StaffDutyAssignment(models.Model):
    duty_type = models.ForeignKey(
        DutyType,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="duty_assignments",
        limit_choices_to={"category": UserCategory.STAFF},
    )
    date = models.DateField()
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="duty_assignments",
    )
    enforces_unique_per_day = models.BooleanField(
        default=False,
        editable=False,
        help_text="Copied from the duty type so unique-per-day duties can be constrained in the database.",
    )

    class Meta:
        ordering = ["-date", "duty_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "duty_type", "date"],
                name="unique_staff_duty_per_date",
            ),
            models.UniqueConstraint(
                fields=["date", "academic_year"],
                condition=models.Q(enforces_unique_per_day=True),
                name="unique_unique_per_day_duty_per_date_year",
            ),
        ]
        indexes = [
            models.Index(fields=["date", "duty_type"]),
            models.Index(fields=["academic_year", "date"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.staff_id and self.staff.category != UserCategory.STAFF:
            errors["staff"] = "Duty assignments require a Staff user."
        if self.duty_type_id:
            self.enforces_unique_per_day = bool(self.duty_type.unique_per_day)
        if self.enforces_unique_per_day and self.date and self.academic_year_id:
            qs = StaffDutyAssignment.objects.filter(
                date=self.date,
                academic_year=self.academic_year,
                enforces_unique_per_day=True,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors["duty_type"] = (
                    f"A unique-per-day duty is already assigned on {self.date}."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.duty_type_id:
            self.enforces_unique_per_day = bool(self.duty_type.unique_per_day)
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.duty_type} — {self.staff} ({self.date})"


class AttendanceEntry(models.Model):
    activity_session = models.ForeignKey(
        ActivitySession,
        on_delete=models.PROTECT,
        related_name="attendance_entries",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="attendance_entries",
    )
    status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_entries_taken",
        limit_choices_to=_ATTENDANCE_ACTOR_LIMIT,
        help_text="Normally a Staff user. Administration may be used only as an override.",
    )
    taken_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_entries_updated",
        null=True,
        blank=True,
        limit_choices_to=_ATTENDANCE_ACTOR_LIMIT,
        help_text="Staff or Administration. Required when correcting status.",
    )
    updated_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["activity_session", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["activity_session", "student"],
                name="unique_attendance_per_student_session",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "activity_session"]),
            models.Index(fields=["status"]),
        ]
        permissions = [
            ("correct_attendance", "Can correct attendance"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.activity_session_id:
            session = self.activity_session
            if session.activity_type_id and not session.activity_type.takes_attendance:
                errors["activity_session"] = (
                    "Attendance cannot be recorded for an activity that does not take attendance."
                )
        if self.taken_by_id and self.taken_by.category not in _ATTENDANCE_ACTOR_CATEGORIES:
            errors["taken_by"] = (
                "Attendance must be taken by a Staff user "
                "(or Administration as an override)."
            )
        if self.updated_by_id and self.updated_by.category not in _ATTENDANCE_ACTOR_CATEGORIES:
            errors["updated_by"] = (
                "Attendance must be updated by a Staff or Administration user."
            )
        if self.pk:
            old_status = (
                AttendanceEntry.objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if old_status is not None and old_status != self.status and not self.updated_by_id:
                errors["updated_by"] = (
                    "A Staff or Administration user is required when changing attendance status."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        old_status = None
        if self.pk:
            old_status = (
                AttendanceEntry.objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
        status_changed = old_status is not None and old_status != self.status
        if status_changed:
            self.updated_at = timezone.now()
        self.full_clean()
        if status_changed:
            with transaction.atomic():
                super().save(*args, **kwargs)
                AttendanceRevision.objects.create(
                    entry=self,
                    old_status=old_status,
                    new_status=self.status,
                    changed_by=self.updated_by,
                    changed_at=self.updated_at or timezone.now(),
                    reason=getattr(self, "_status_change_reason", "") or "",
                )
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} — {self.activity_session} — {self.status}"


class AttendanceRevision(models.Model):
    """Narrow history of AttendanceEntry status changes only."""

    entry = models.ForeignKey(
        AttendanceEntry,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    old_status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    new_status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_revisions",
        limit_choices_to=_ATTENDANCE_ACTOR_LIMIT,
    )
    changed_at = models.DateTimeField()
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["entry", "changed_at"]),
        ]

    def __str__(self):
        return f"{self.entry_id}: {self.old_status} → {self.new_status}"
