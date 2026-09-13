from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0024_student_table_layout"),
    ]

    operations = [
        migrations.AlterField(
            model_name="student",
            name="blood_group",
            field=models.CharField(
                blank=True,
                choices=[
                    ("A+", "A+"),
                    ("A-", "A-"),
                    ("B+", "B+"),
                    ("B-", "B-"),
                    ("AB+", "AB+"),
                    ("AB-", "AB-"),
                    ("O+", "O+"),
                    ("O-", "O-"),
                ],
                max_length=8,
            ),
        ),
    ]
