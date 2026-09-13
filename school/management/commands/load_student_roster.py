"""Load hypothetical JNV students from a CSV roster."""

import csv
from datetime import date, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from school.models import (
    AcademicYear,
    ClassSection,
    Gender,
    House,
    Student,
    StudentHouseMembership,
)


GRADE_LABELS = {
    "6": "VI",
    "7": "VII",
    "8": "VIII",
    "9": "IX",
    "10": "X",
    "11": "XI",
    "12": "XII",
}

SECTION_LABELS = {
    "Section A": "A",
    "Section B": "B",
    "Science Stream": "Science",
    "Commerce Stream": "Commerce",
}

GENDER_MAP = {
    "Male": Gender.MALE,
    "Female": Gender.FEMALE,
}


class Command(BaseCommand):
    help = "Import students from the JNV hypothetical students CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default="/home/sona/Downloads/jnv_hypothetical_students_600.csv",
        )

    def handle(self, *args, **options):
        path = Path(options["csv_path"])
        if not path.is_file():
            raise CommandError(f"CSV not found: {path}")

        year = AcademicYear.objects.filter(is_current=True).first()
        if year is None:
            raise CommandError("Create a current academic year before importing students.")

        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise CommandError("CSV has no student rows.")

        created = 0
        updated = 0
        with transaction.atomic():
            sections = {}
            houses = {}
            used_rolls = {}
            for row in rows:
                section = self._class_section(row, sections)
                house = self._house(row["House Allocation"].strip(), houses)
                student, was_created = self._upsert_student(row, year, section, used_rolls)
                StudentHouseMembership.objects.update_or_create(
                    student=student,
                    academic_year=year,
                    defaults={"house": house},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(rows)} students: {created} created, {updated} updated "
                f"for {year}."
            )
        )

    def _class_section(self, row, cache):
        grade_key = str(row["Class"]).strip()
        section_key = row["Section/Stream"].strip()
        grade = GRADE_LABELS.get(grade_key)
        section = SECTION_LABELS.get(section_key)
        if grade_key in {"11", "12"} and section_key.startswith("Section"):
            section = "Science"
        if not grade or not section:
            raise CommandError(
                f"Unknown class/section: {grade_key!r} / {section_key!r}"
            )
        display = f"{grade}-{section}"
        if display not in cache:
            cache[display] = ClassSection.objects.get_or_create(
                grade_name=grade,
                section_name=section,
                defaults={"display_name": display, "is_active": True},
            )[0]
        return cache[display]

    def _house(self, name, cache):
        if name not in cache:
            cache[name] = House.objects.get_or_create(name=name)[0]
        return cache[name]

    def _next_roll(self, year, section, used_rolls):
        key = (year.pk, section.pk)
        if key not in used_rolls:
            used_rolls[key] = set(
                Student.objects.filter(
                    academic_year=year,
                    class_section=section,
                ).values_list("roll_number", flat=True)
            )
        roll = 1
        taken = used_rolls[key]
        while roll in taken:
            roll += 1
        taken.add(roll)
        return roll

    def _upsert_student(self, row, year, section, used_rolls):
        admission = row["Admission ID"].strip()
        first_name, last_name = self._split_name(row["Student Name"].strip())
        gender = GENDER_MAP.get(row["Gender"].strip())
        if gender is None:
            raise CommandError(f"Unknown gender for {admission}: {row['Gender']!r}")
        student = Student.objects.filter(admission_number=admission).first()
        if student is None:
            student = Student(
                admission_number=admission,
                roll_number=self._next_roll(year, section, used_rolls),
                first_name=first_name,
                last_name=last_name,
                date_of_birth=self._placeholder_dob(row),
                gender=gender,
                class_section=section,
                academic_year=year,
                is_active=True,
            )
            student.save()
            return student, True
        student.first_name = first_name
        student.last_name = last_name
        student.gender = gender
        student.class_section = section
        student.academic_year = year
        student.is_active = True
        student.save()
        return student, False

    def _split_name(self, full_name):
        parts = full_name.split()
        if len(parts) == 1:
            return parts[0], parts[0]
        return " ".join(parts[:-1]), parts[-1]

    def _placeholder_dob(self, row):
        klass = int(str(row["Class"]).strip())
        year = 2020 - klass
        digits = "".join(ch for ch in row["Admission ID"] if ch.isdigit()) or "1"
        return date(year, 1, 1) + timedelta(days=int(digits[-4:]) % 365)
