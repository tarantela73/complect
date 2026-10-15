import json

from django.core.management import BaseCommand, CommandError

from apps.users.tasks.identity_merge_maintenance import expire_pending_identity_merges


class Command(BaseCommand):
    help = "Expire bounded pending Identity merge approvals."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        if not 1 <= options["batch_size"] <= 500:
            raise CommandError("--batch-size must be between 1 and 500")
        result = expire_pending_identity_merges.run(batch_size=options["batch_size"])
        self.stdout.write(json.dumps(result, sort_keys=True))
