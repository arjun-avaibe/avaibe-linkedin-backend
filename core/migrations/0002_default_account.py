"""Fresh installs start with one company-page account so the app isn't empty.
(Skipped automatically when accounts already exist, e.g. after importing old data.)"""

from django.db import migrations


def create_default(apps, schema_editor):
    Account = apps.get_model("core", "Account")
    if not Account.objects.exists():
        Account.objects.create(name="AVAIBE Test Page", kind="company", is_test=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_default, migrations.RunPython.noop)]
