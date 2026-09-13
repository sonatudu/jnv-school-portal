"""Load the JNV staff roster into users, designations, and house assignments."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Designation, UserCategory
from school.models import (
    AcademicYear,
    House,
    HouseMasterAssignment,
    HouseStaffRole,
    TeacherProfile,
)


DEFAULT_PASSWORD = "Navodaya@2026"
User = get_user_model()

ADMIN = UserCategory.ADMINISTRATION
STAFF = UserCategory.STAFF

ROSTER = [
    # Administration
    ("janardan.singh", "Janardan", "Singh", ADMIN, "Principal", False, True, True),
    ("sunita.rajan", "Sunita", "Rajan", ADMIN, "Vice-Principal", False, True, False),
    # PGTs
    ("alok.kumar.sharma", "Alok Kumar", "Sharma", STAFF, "PGT Physics", True, False, False),
    ("amitava.biswas", "Amitava", "Biswas", STAFF, "PGT Chemistry", True, False, False),
    ("priya.deshmukh", "Priya", "Deshmukh", STAFF, "PGT Biology", True, False, False),
    ("subhash.chandra.bose", "Subhash Chandra", "Bose", STAFF, "PGT Mathematics", True, False, False),
    ("rajesh.varma", "Rajesh", "Varma", STAFF, "PGT Computer Science", True, False, False),
    ("aranya.sen", "Aranya", "Sen", STAFF, "PGT English", True, False, False),
    ("ramesh.chandra.mishra", "Ramesh Chandra", "Mishra", STAFF, "PGT Hindi", True, False, False),
    ("neha.aggarwal", "Neha", "Aggarwal", STAFF, "PGT Commerce", True, False, False),
    ("shalini.iyer", "Shalini", "Iyer", STAFF, "PGT Economics", True, False, False),
    ("mahendra.prasad.singh", "Mahendra Prasad", "Singh", STAFF, "PGT Geography", True, False, False),
    ("tanvi.bharadwaj", "Tanvi", "Bharadwaj", STAFF, "PGT Political Science", True, False, False),
    # TGTs
    ("kavita.reddy", "Kavita", "Reddy", STAFF, "TGT Mathematics (Section A)", True, False, False),
    ("rakesh.jhunjhunwala", "Rakesh", "Jhunjhunwala", STAFF, "TGT Mathematics (Section B)", True, False, False),
    ("joydeep.mukherjee", "Joydeep", "Mukherjee", STAFF, "TGT Science (Physics/Math)", True, False, False),
    ("sandeep.mahapatra", "Sandeep", "Mahapatra", STAFF, "TGT Science (Chemistry/Biology)", True, False, False),
    ("vikram.jeet.singh", "Vikram Jeet", "Singh", STAFF, "TGT English", True, False, False),
    ("vinod.kumar.shukla", "Vinod Kumar", "Shukla", STAFF, "TGT Hindi", True, False, False),
    ("preeti.talwar", "Preeti", "Talwar", STAFF, "TGT Social Science (History/Civics)", True, False, False),
    ("anjali.nair", "Anjali", "Nair", STAFF, "TGT Social Science (Geography/Economics)", True, False, False),
    ("hemant.soren", "Hemant", "Soren", STAFF, "TGT Regional Language (Santhali/Odia)", True, False, False),
    ("harish.shankar", "Harish", "Shankar", STAFF, "TGT Third Language (Sanskrit)", True, False, False),
    ("birsa.murmu", "Birsa", "Murmu", STAFF, "TGT Third Language (Regional)", True, False, False),
    ("meenakshi.joshi", "Meenakshi", "Joshi", STAFF, "TGT Art", True, False, False),
    # Creative arts & health
    ("debojit.bhattacharya", "Debojit", "Bhattacharya", STAFF, "Music Teacher", True, False, False),
    ("amrita.shergill", "Amrita", "Shergill", STAFF, "Art Teacher", True, False, False),
    ("gurpreet.singh", "Gurpreet", "Singh", STAFF, "PET (Male)", True, False, False),
    ("suman.lata", "Suman", "Lata", STAFF, "PET (Female)", True, False, False),
    ("v.s.lakshmi", "V. S.", "Lakshmi", STAFF, "Librarian", False, False, False),
    ("mary.dsouza", "Mary", "D'Souza", STAFF, "Staff Nurse", False, False, False),
    # Laboratory
    ("tapan.kumar.mahato", "Tapan Kumar", "Mahato", STAFF, "Lab Assistant (Physics)", False, False, False),
    ("mousumi.dey", "Mousumi", "Dey", STAFF, "Lab Assistant (Chemistry)", False, False, False),
    ("sanjay.hansda", "Sanjay", "Hansda", STAFF, "Lab Assistant (Biology)", False, False, False),
    ("ashish.karmakar", "Ashish", "Karmakar", STAFF, "Computer Lab Attendant", False, False, False),
    # Office
    ("k.p.raghunathan", "K. P.", "Raghunathan", STAFF, "Office Superintendent", False, True, False),
    ("sarita.hembram", "Sarita", "Hembram", STAFF, "Upper Division Clerk", False, False, False),
    ("rahul.tudu", "Rahul", "Tudu", STAFF, "Lower Division Clerk", False, False, False),
    ("bikram.singh", "Bikram", "Singh", STAFF, "Store Keeper", False, False, False),
    # Mess & campus
    ("jagdish.prasad", "Jagdish", "Prasad", STAFF, "Catering Assistant", False, False, False),
    ("ramlal.goala", "Ramlal", "Goala", STAFF, "Head Cook", False, False, False),
    ("sunil.munda", "Sunil", "Munda", STAFF, "Mess Helper", False, False, False),
    ("arjun.naik", "Arjun", "Naik", STAFF, "Mess Helper", False, False, False),
    ("malti.soren", "Malti", "Soren", STAFF, "Mess Helper", False, False, False),
    ("budhan.singh", "Budhan", "Singh", STAFF, "Chowkidar", False, False, False),
    ("gagan.bauri", "Gagan", "Bauri", STAFF, "Sweeper-cum-Chowkidar", False, False, False),
]

HOUSES = ("Aravali", "Nilgiri", "Shivalik", "Udaygiri")

HOUSE_ASSIGNMENTS = (
    ("Aravali", "rajesh.varma", HouseStaffRole.HOUSE_TEACHER),
    ("Aravali", "priya.deshmukh", HouseStaffRole.ASSISTANT_HOUSE_TEACHER),
    ("Nilgiri", "vikram.jeet.singh", HouseStaffRole.HOUSE_TEACHER),
    ("Shivalik", "sandeep.mahapatra", HouseStaffRole.HOUSE_TEACHER),
    ("Udaygiri", "anjali.nair", HouseStaffRole.HOUSE_TEACHER),
)


class Command(BaseCommand):
    help = "Create JNV staff users, designations, teacher profiles, and house teachers."

    def handle(self, *args, **options):
        created_users = 0
        updated_users = 0
        with transaction.atomic():
            year = self._ensure_year()
            houses = {name: House.objects.get_or_create(name=name)[0] for name in HOUSES}
            for row in ROSTER:
                created = self._upsert_user(row)
                if created:
                    created_users += 1
                else:
                    updated_users += 1
            assigned = 0
            for house_name, username, role in HOUSE_ASSIGNMENTS:
                staff = User.objects.get(username=username)
                _, was_created = HouseMasterAssignment.objects.get_or_create(
                    house=houses[house_name],
                    academic_year=year,
                    role=role,
                    defaults={"staff": staff},
                )
                if was_created:
                    assigned += 1
                else:
                    assignment = HouseMasterAssignment.objects.get(
                        house=houses[house_name],
                        academic_year=year,
                        role=role,
                    )
                    if assignment.staff_id != staff.pk:
                        assignment.staff = staff
                        assignment.save()
                        assigned += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Staff roster loaded: {created_users} created, {updated_users} updated, "
                f"{assigned} house assignments set. "
                f"New accounts use password {DEFAULT_PASSWORD}."
            )
        )

    def _ensure_year(self):
        from school.hypothetical_attendance import ensure_hypothetical_academic_years

        years = ensure_hypothetical_academic_years()
        current = AcademicYear.objects.filter(is_current=True).first()
        if current:
            return current
        return years[-1] if years else None

    def _upsert_user(self, row):
        username, first_name, last_name, category, title, is_teacher, is_staff, is_superuser = row
        designation, _ = Designation.objects.get_or_create(
            name=title,
            defaults={"category": category},
        )
        user = User.objects.filter(username=username).first()
        created = False
        if user is None:
            user = User.objects.create_user(
                username=username,
                password=DEFAULT_PASSWORD,
                category=category,
            )
            created = True
        user.first_name = first_name
        user.last_name = last_name
        user.category = category
        user.designation = designation
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.is_active = True
        user.save()
        if is_teacher:
            TeacherProfile.objects.get_or_create(user=user)
        return created
