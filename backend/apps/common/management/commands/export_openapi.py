import json
import sys

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Export OpenAPI schema as JSON. Pipe to a file or use --output."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output",
            default="-",
            help="Output file path (default: stdout).",
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=2,
            help="JSON indentation level (default: 2).",
        )

    def handle(self, *args, **options) -> None:
        from config.urls import api  # noqa: PLC0415

        schema = api.get_openapi_schema()
        payload = json.dumps(schema, indent=options["indent"], ensure_ascii=False)

        output_path: str = options["output"]
        if output_path == "-":
            sys.stdout.write(payload)
            sys.stdout.write("\n")
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.write("\n")
            self.stdout.write(self.style.SUCCESS(f"OpenAPI schema written to {output_path}"))
