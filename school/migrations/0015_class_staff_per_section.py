import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0014_class_grade_staff"),
    ]

    operations = [
        migrations.DeleteModel(name="ClassGradeOptions"),
        migrations.DeleteModel(name="ClassGradeStaffAssignment"),
        migrations.CreateModel(
            name="ClassGradeOptions",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "show_assistant_class_teacher",
                    models.BooleanField(
                        default=False,
                        help_text="If enabled, this class page shows an Assistant Class Teacher.",
                    ),
                ),
                (
                    "academic_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="class_grade_options",
                        to="school.academicyear",
                    ),
                ),
                (
                    "class_section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="grade_options",
                        to="school.classsection",
                    ),
                ),
            ],
            options={
                "verbose_name": "class options",
                "verbose_name_plural": "class options",
                "ordering": ["-academic_year", "class_section"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("academic_year", "class_section"),
                        name="unique_class_grade_options_per_year",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ClassGradeStaffAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("class_teacher", "Class Teacher"),
                            (
                                "assistant_class_teacher",
                                "Assistant Class Teacher",
                            ),
                        ],
                        default="class_teacher",
                        max_length=32,
                    ),
                ),
                (
                    "academic_year",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="class_grade_staff_assignments",
                        to="school.academicyear",
                    ),
                ),
                (
                    "class_section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="grade_staff_assignments",
                        to="school.classsection",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="class_grade_assignments",
                        to="school.teacherprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "class grade staff assignment",
                "verbose_name_plural": "class grade staff assignments",
                "ordering": ["-academic_year", "class_section", "role"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("class_section", "academic_year", "role"),
                        name="unique_class_grade_staff_role_per_year",
                    )
                ],
            },
        ),
    ]
