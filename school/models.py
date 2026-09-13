from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from accounts.models import UserCategory


class Gender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"
    OTHER = "other", "Other"


class SocialCategory(models.TextChoices):
    GENERAL = "general", "General"
    OBC = "obc", "OBC"
    SC = "sc", "SC"
    ST = "st", "ST"
    EWS = "ews", "EWS"


class AreaType(models.TextChoices):
    RURAL = "rural", "Rural"
    URBAN = "urban", "Urban"


class BloodGroup(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


GRADE_SENIORITY = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
}


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
        "class",
        max_length=20,
        help_text="Class/grade label stored in the database, e.g. VI or XI.",
    )
    section_name = models.CharField(
        "section",
        max_length=30,
        help_text="Section or stream stored in the database, e.g. A or Science.",
    )
    display_name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Readable name, e.g. VI-A or XI-Science.",
    )
    is_active = models.BooleanField(default=True)
    grade_number = models.PositiveSmallIntegerField(
        default=0,
        editable=False,
        help_text="Sort key so classes appear in seniority order (VI before IX).",
    )

    class Meta:
        ordering = ["grade_number", "section_name", "display_name"]
        verbose_name = "class"
        verbose_name_plural = "classes"
        constraints = [
            models.UniqueConstraint(
                fields=["grade_name", "section_name"],
                name="unique_grade_section",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["grade_number", "section_name"]),
        ]

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = f"{self.grade_name}-{self.section_name}"
        self.grade_number = GRADE_SENIORITY.get(self.grade_name, 99)
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
    father_name = models.CharField("father's name", max_length=120, blank=True)
    mother_name = models.CharField("mother's name", max_length=120, blank=True)
    social_category = models.CharField(
        "category",
        max_length=16,
        choices=SocialCategory.choices,
        blank=True,
    )
    area_type = models.CharField(
        max_length=16,
        choices=AreaType.choices,
        blank=True,
        help_text="Rural or urban, as used in JNV admission.",
    )
    native_district = models.CharField(max_length=80, blank=True)
    blood_group = models.CharField(
        max_length=8,
        choices=BloodGroup.choices,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="students",
        verbose_name="class",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="students",
    )

    class Meta:
        ordering = [
            "class_section__grade_number",
            "class_section__section_name",
            "roll_number",
            "last_name",
            "first_name",
        ]
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


class StudentBiodataRow(models.Model):
    """Admin-added extra biodata field for one student."""

    student = models.ForeignKey(
        Student,
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
                fields=["student", "label"],
                name="unique_student_biodata_label",
            ),
        ]
        verbose_name = "student biodata row"
        verbose_name_plural = "student biodata rows"

    def __str__(self):
        return f"{self.student}: {self.label}"


class StudentTableColumn(models.Model):
    """School-wide extra column on class Students tables."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"

    label = models.CharField(max_length=120, unique=True)
    field_type = models.CharField(
        max_length=16,
        choices=FieldType.choices,
        default=FieldType.TEXT,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "student table column"
        verbose_name_plural = "student table columns"

    def __str__(self):
        return self.label


class StudentTableLayout(models.Model):
    """Order and visibility for one Students-table column (built-in or extra)."""

    column_key = models.CharField(max_length=80, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "student table layout"
        verbose_name_plural = "student table layouts"

    def __str__(self):
        return self.column_key


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


class StudentGuardian(models.Model):
    """A parent profile that may view this student's attendance."""

    parent_profile = models.ForeignKey(
        ParentProfile,
        on_delete=models.PROTECT,
        related_name="guardian_links",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="guardian_links",
    )

    class Meta:
        ordering = ["parent_profile", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent_profile", "student"],
                name="unique_parent_student_guardian",
            ),
        ]
        verbose_name = "student guardian"
        verbose_name_plural = "student guardians"

    def __str__(self):
        return f"{self.parent_profile} — {self.student}"


class House(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


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
        verbose_name="class",
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


class ClassStaffRole(models.TextChoices):
    CLASS_TEACHER = "class_teacher", "Class Teacher"
    ASSISTANT_CLASS_TEACHER = "assistant_class_teacher", "Assistant Class Teacher"


class ClassGradeOptions(models.Model):
    """Admin options for one class (e.g. VI-A) in an academic year."""

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="class_grade_options",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="grade_options",
    )
    show_assistant_class_teacher = models.BooleanField(
        default=False,
        help_text="If enabled, this class page shows an Assistant Class Teacher.",
    )

    class Meta:
        ordering = ["-academic_year", "class_section"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "class_section"],
                name="unique_class_grade_options_per_year",
            ),
        ]
        verbose_name = "class options"
        verbose_name_plural = "class options"

    def __str__(self):
        return f"{self.class_section} ({self.academic_year})"


class ClassGradeStaffAssignment(models.Model):
    """Class Teacher or Assistant Class Teacher for one class (e.g. VI-A)."""

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="class_grade_assignments",
    )
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.PROTECT,
        related_name="grade_staff_assignments",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="class_grade_staff_assignments",
    )
    role = models.CharField(
        max_length=32,
        choices=ClassStaffRole.choices,
        default=ClassStaffRole.CLASS_TEACHER,
    )

    class Meta:
        ordering = ["-academic_year", "class_section", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_section", "academic_year", "role"],
                name="unique_class_grade_staff_role_per_year",
            ),
            models.UniqueConstraint(
                fields=["teacher", "class_section", "academic_year"],
                name="unique_teacher_per_class_section_year",
            ),
        ]
        verbose_name = "class grade staff assignment"
        verbose_name_plural = "class grade staff assignments"

    def clean(self):
        super().clean()
        if self.teacher_id and self.teacher.user.category != UserCategory.STAFF:
            raise ValidationError(
                {"teacher": "Class teacher assignments require a Staff teacher profile."}
            )
        if self.teacher_id and self.class_section_id and self.academic_year_id:
            clash = ClassGradeStaffAssignment.objects.filter(
                teacher_id=self.teacher_id,
                class_section_id=self.class_section_id,
                academic_year_id=self.academic_year_id,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError(
                    {
                        "teacher": (
                            "Class teacher and assistant class teacher must be "
                            "different people."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.teacher} — {self.get_role_display()}, {self.class_section} "
            f"({self.academic_year})"
        )


class HouseStaffRole(models.TextChoices):
    HOUSE_TEACHER = "house_teacher", "House Teacher"
    ASSISTANT_HOUSE_TEACHER = "assistant_house_teacher", "Assistant House Teacher"


class HouseMasterAssignment(models.Model):
    """Staff assigned to a house for an academic year as House Teacher or Assistant."""

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
    role = models.CharField(
        max_length=32,
        choices=HouseStaffRole.choices,
        default=HouseStaffRole.HOUSE_TEACHER,
    )

    class Meta:
        ordering = ["-academic_year", "house", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "house", "academic_year"],
                name="unique_house_master_assignment",
            ),
            models.UniqueConstraint(
                fields=["house", "academic_year", "role"],
                name="unique_house_staff_role_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year", "house"]),
            models.Index(fields=["academic_year", "staff"]),
        ]
        verbose_name = "house teacher assignment"
        verbose_name_plural = "house teacher assignments"

    def clean(self):
        super().clean()
        if self.staff_id and self.staff.category != UserCategory.STAFF:
            raise ValidationError(
                {"staff": "House teacher assignments require a Staff user."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.staff} — {self.get_role_display()}, {self.house} "
            f"({self.academic_year})"
        )


class AudienceKind(models.TextChoices):
    CLASS = "class", "Class"
    HOUSE = "house", "House"
    SCHOOL = "school", "Whole school"
    STUDENT_GROUP = "student_group", "Named student group"
    SELECTED_STUDENTS = "selected_students", "Selected students"


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    ON_DUTY = "on_duty", "OD"
    SICK = "sick", "Sick"
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


class CircularAudience(models.TextChoices):
    ALL = "all", "Whole vidyalaya"
    STAFF = "staff", "Staff and administration"
    PARENTS = "parents", "Parents"


class OutingStatus(models.TextChoices):
    APPROVED = "approved", "Approved"
    OUT = "out", "Out of campus"
    RETURNED = "returned", "Returned"
    CANCELLED = "cancelled", "Cancelled"


class VidyalayaProfile(models.Model):
    """Singleton identity for JNV East Singhbhum (Balikudia, Baharagora)."""

    name = models.CharField(max_length=160, default="Jawahar Navodaya Vidyalaya")
    district = models.CharField(max_length=80, default="East Singhbhum")
    state = models.CharField(max_length=80, default="Jharkhand")
    nvs_region = models.CharField(max_length=80, default="Patna")
    campus = models.CharField(max_length=120, default="Balikudia, Baharagora")
    udise_code = models.CharField(max_length=20, blank=True)
    established_year = models.PositiveSmallIntegerField(default=2001)
    motto = models.CharField(max_length=80, default="Prajñānam Brahma")
    about = models.TextField(
        blank=True,
        default=(
            "Jawahar Navodaya Vidyalaya, East Singhbhum is a residential "
            "co-educational school at Balikudia, Baharagora (PIN 832101), "
            "under Navodaya Vidyalaya Samiti, Patna Region. Classes VI–XII, "
            "CBSE, house system, and the Navodaya daily routine of PT, "
            "assembly, studies, games, and night roll call."
        ),
    )

    class Meta:
        verbose_name = "vidyalaya profile"
        verbose_name_plural = "vidyalaya profile"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def display_name(self):
        if self.district:
            return f"{self.name}, {self.district}"
        return self.name

    @property
    def short_name(self):
        return f"JNV {self.district}" if self.district else "JNV"

    @property
    def place_line(self):
        parts = []
        if self.campus:
            parts.append(self.campus)
        if self.district and self.district not in (self.campus or ""):
            parts.append(self.district)
        if self.state:
            parts.append(self.state)
        return " · ".join(parts)

    def __str__(self):
        return self.display_name


class Circular(models.Model):
    """Principal / office circular for staff, parents, or the whole vidyalaya."""

    title = models.CharField(max_length=160)
    body = models.TextField()
    audience = models.CharField(
        max_length=16,
        choices=CircularAudience.choices,
        default=CircularAudience.ALL,
    )
    published_on = models.DateField()
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="circulars",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-published_on", "-pk"]
        indexes = [
            models.Index(fields=["is_published", "audience", "published_on"]),
        ]

    def __str__(self):
        return self.title


class MessMenu(models.Model):
    """Daily four-meal mess menu (JNV residential dining)."""

    date = models.DateField(unique=True)
    breakfast = models.CharField(max_length=200)
    lunch = models.CharField(max_length=200)
    evening_snacks = models.CharField(max_length=200)
    dinner = models.CharField(max_length=200)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Mess menu {self.date}"


class OutingPass(models.Model):
    """Campus outing / hospital / official duty gate pass."""

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="outing_passes",
    )
    date = models.DateField()
    departure_time = models.TimeField()
    expected_return = models.TimeField(null=True, blank=True)
    purpose = models.CharField(max_length=120)
    destination = models.CharField(max_length=120, blank=True)
    escort_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=16,
        choices=OutingStatus.choices,
        default=OutingStatus.APPROVED,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="outing_passes_issued",
        limit_choices_to={"category__in": _ATTENDANCE_ACTOR_CATEGORIES},
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "-departure_time"]
        indexes = [
            models.Index(fields=["date", "status"]),
            models.Index(fields=["student", "date"]),
        ]

    def __str__(self):
        return f"{self.student} · {self.date} · {self.purpose}"


class StudentOfficeRole(models.TextChoices):
    SCHOOL_CAPTAIN = "school_captain", "School captain"
    SCHOOL_VICE_CAPTAIN = "school_vice_captain", "School vice-captain"
    HOUSE_CAPTAIN = "house_captain", "House captain"
    HOUSE_VICE_CAPTAIN = "house_vice_captain", "House vice-captain"
    PREFECT = "prefect", "Prefect"
    MESS_PREFECT = "mess_prefect", "Mess prefect"
    GAMES_CAPTAIN = "games_captain", "Games captain"
    CCA_CAPTAIN = "cca_captain", "CCA captain"


class StudentOffice(models.Model):
    """House and school student leadership for an academic year."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="offices"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="student_offices"
    )
    house = models.ForeignKey(
        House, on_delete=models.SET_NULL, null=True, blank=True, related_name="offices"
    )
    role = models.CharField(max_length=32, choices=StudentOfficeRole.choices)

    class Meta:
        ordering = ["academic_year", "role", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "role", "house", "student"],
                name="unique_student_office",
            ),
        ]

    def __str__(self):
        place = f" · {self.house}" if self.house_id else ""
        return f"{self.get_role_display()}{place} · {self.student}"


class CompetitionKind(models.TextChoices):
    SPORTS = "sports", "Sports / games"
    CULTURAL = "cultural", "Cultural"
    LITERARY = "literary", "Literary"
    QUIZ = "quiz", "Quiz"
    CCA = "cca", "CCA"


class HouseCompetition(models.Model):
    """Inter-house fixture (athletics, cultural, literary, pace-setting)."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="house_competitions"
    )
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=CompetitionKind.choices)
    held_on = models.DateField()
    venue = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-held_on", "name"]

    def __str__(self):
        return f"{self.name} ({self.held_on})"


class HouseCompetitionResult(models.Model):
    competition = models.ForeignKey(
        HouseCompetition, on_delete=models.CASCADE, related_name="results"
    )
    house = models.ForeignKey(House, on_delete=models.CASCADE, related_name="competition_results")
    points = models.PositiveSmallIntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "house"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "house"],
                name="unique_competition_house",
            ),
        ]

    def __str__(self):
        return f"{self.house} · {self.points} pts"


class SickBayVisit(models.Model):
    """Staff-nurse sick-bay register."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="sick_bay_visits"
    )
    visited_on = models.DateField()
    visited_at = models.TimeField()
    complaint = models.CharField(max_length=160)
    treatment = models.CharField(max_length=200, blank=True)
    referred_out = models.BooleanField(default=False)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sick_bay_visits",
        limit_choices_to={"category__in": _ATTENDANCE_ACTOR_CATEGORIES},
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-visited_on", "-visited_at"]

    def __str__(self):
        return f"{self.student} · {self.visited_on} · {self.complaint}"


class VisitorPass(models.Model):
    """Sunday meeting / authorised visitor at the gate."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="visitor_passes"
    )
    visited_on = models.DateField()
    visitor_name = models.CharField(max_length=120)
    relation = models.CharField(max_length=80, blank=True)
    purpose = models.CharField(max_length=120, default="Sunday meeting")
    in_time = models.TimeField(null=True, blank=True)
    out_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["-visited_on", "visitor_name"]

    def __str__(self):
        return f"{self.visitor_name} · {self.student}"


class LibraryBook(models.Model):
    accession_no = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=120, blank=True)
    subject = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["accession_no"]

    def __str__(self):
        return f"{self.accession_no} · {self.title}"


class LibraryIssue(models.Model):
    book = models.ForeignKey(LibraryBook, on_delete=models.CASCADE, related_name="issues")
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="library_issues"
    )
    issued_on = models.DateField()
    due_on = models.DateField()
    returned_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_on"]

    def __str__(self):
        return f"{self.book} → {self.student}"


class ExamTerm(models.Model):
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="exam_terms"
    )
    name = models.CharField(max_length=80)
    starts_on = models.DateField()
    ends_on = models.DateField()

    class Meta:
        ordering = ["-starts_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "name"],
                name="unique_exam_term_year",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"


class AssessmentMark(models.Model):
    term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name="marks")
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="assessment_marks"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="assessment_marks"
    )
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=1)
    max_marks = models.PositiveSmallIntegerField(default=40)

    class Meta:
        ordering = ["student", "subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["term", "student", "subject"],
                name="unique_mark_term_student_subject",
            ),
        ]

    def __str__(self):
        return f"{self.student} · {self.subject} · {self.marks_obtained}"


class CommitteeKind(models.TextChoices):
    VMC = "vmc", "Vidyalaya Management Committee"
    VAC = "vac", "Vidyalaya Advisory Committee"
    PAC = "pac", "Parent committee"


class CommitteeSeat(models.Model):
    kind = models.CharField(max_length=8, choices=CommitteeKind.choices, default=CommitteeKind.VMC)
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="committee_seats"
    )
    role = models.CharField(max_length=120)
    member_name = models.CharField(max_length=160)
    organisation = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["kind", "pk"]

    def __str__(self):
        return f"{self.role} · {self.member_name}"


class EventKind(models.TextChoices):
    ASSEMBLY = "assembly", "Assembly / national day"
    CCA = "cca", "CCA"
    GAMES = "games", "Games / sports"
    PACE = "pace", "Pace-setting"
    MIGRATION = "migration", "Migration"
    OTHER = "other", "Other"


class VidyalayaEvent(models.Model):
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="events"
    )
    title = models.CharField(max_length=160)
    held_on = models.DateField()
    kind = models.CharField(max_length=16, choices=EventKind.choices, default=EventKind.CCA)
    venue = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-held_on", "title"]

    def __str__(self):
        return f"{self.title} ({self.held_on})"


class VvnEntry(models.Model):
    """Hypothetical Vidyalaya Vikas Nidhi (VVN) charge for a student-year."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="vvn_entries"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="vvn_entries"
    )
    amount = models.PositiveIntegerField(default=1500)
    is_paid = models.BooleanField(default=False)
    remark = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["student"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                name="unique_vvn_student_year",
            ),
        ]
        verbose_name = "VVN entry"
        verbose_name_plural = "VVN entries"

    def __str__(self):
        status = "paid" if self.is_paid else "due"
        return f"{self.student} · {self.academic_year} · {status}"


class MigrationRecord(models.Model):
    """Class XI Navodaya migration (incoming or outgoing)."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="migrations"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="migrations"
    )
    direction = models.CharField(
        max_length=8,
        choices=[("in", "Incoming"), ("out", "Outgoing")],
    )
    other_jnv = models.CharField(max_length=160)
    stream = models.CharField(max_length=40, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["direction", "student"]

    def __str__(self):
        return f"{self.student} · {self.direction} · {self.other_jnv}"
