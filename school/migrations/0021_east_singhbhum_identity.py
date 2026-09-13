from django.db import migrations, models


EAST_SINGHBHUM = {
    "name": "Jawahar Navodaya Vidyalaya",
    "district": "East Singhbhum",
    "state": "Jharkhand",
    "nvs_region": "Patna",
    "campus": "Balikudia, Baharagora",
    "established_year": 2001,
    "motto": "Prajñānam Brahma",
    "about": (
        "Jawahar Navodaya Vidyalaya, East Singhbhum is a residential "
        "co-educational school at Balikudia, Baharagora (PIN 832101), "
        "under Navodaya Vidyalaya Samiti, Patna Region. Classes VI–XII, "
        "CBSE, house system, and the Navodaya daily routine of PT, "
        "assembly, studies, games, and night roll call."
    ),
}


def apply_identity(apps, schema_editor):
    VidyalayaProfile = apps.get_model("school", "VidyalayaProfile")
    profile, _ = VidyalayaProfile.objects.get_or_create(pk=1)
    for field, value in EAST_SINGHBHUM.items():
        setattr(profile, field, value)
    profile.save()


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0020_vidyalaya_life"),
    ]

    operations = [
        migrations.AddField(
            model_name="vidyalayaprofile",
            name="campus",
            field=models.CharField(default="Balikudia, Baharagora", max_length=120),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="district",
            field=models.CharField(default="East Singhbhum", max_length=80),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="state",
            field=models.CharField(default="Jharkhand", max_length=80),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="nvs_region",
            field=models.CharField(default="Patna", max_length=80),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="established_year",
            field=models.PositiveSmallIntegerField(default=2001),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="motto",
            field=models.CharField(default="Prajñānam Brahma", max_length=80),
        ),
        migrations.AlterField(
            model_name="vidyalayaprofile",
            name="about",
            field=models.TextField(
                blank=True,
                default=(
                    "Jawahar Navodaya Vidyalaya, East Singhbhum is a residential "
                    "co-educational school at Balikudia, Baharagora (PIN 832101), "
                    "under Navodaya Vidyalaya Samiti, Patna Region. Classes VI–XII, "
                    "CBSE, house system, and the Navodaya daily routine of PT, "
                    "assembly, studies, games, and night roll call."
                ),
            ),
        ),
        migrations.RunPython(apply_identity, migrations.RunPython.noop),
    ]
