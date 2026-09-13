"""Create hypothetical weekday sessions and random attendance for every student."""

from django.core.management.base import BaseCommand, CommandError

from school.hypothetical_attendance import seed_hypothetical_attendance


class Command(BaseCommand):
    help = (
        "Create at least two hypothetical academic years if needed, copy current "
        "rosters and staff into the other year, generate class and house sessions "
        "for recent weekdays in each year, and fill random hypothetical attendance."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=5)
        parser.add_argument("--seed", type=int, default=2026)

    def handle(self, *args, **options):
        try:
            result = seed_hypothetical_attendance(
                day_count=options["days"],
                seed=options["seed"],
            )
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        years = ", ".join(result.get("years") or [])
        self.stdout.write(
            self.style.SUCCESS(
                f"Years {years}; dates {result['dates'][0]}–{result['dates'][-1]}: "
                f"{result['sessions']} sessions, "
                f"created {result['created']} attendance marks, "
                f"skipped {result['skipped']} already marked."
            )
        )
        for item in result["generation"]:
            if item.errors or item.warnings:
                self.stdout.write(item.summary())
        from school.jnv_operations import seed_jnv_operations
        from school.vidyalaya import seed_vidyalaya_life

        life = seed_vidyalaya_life(seed=options["seed"])
        ops = seed_jnv_operations(seed=options["seed"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Vidyalaya {life['profile']}: {life['menus']} mess menus, "
                f"{life['circulars']} circulars. Operations: {ops}."
            )
        )
