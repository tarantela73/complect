import type { ConfigFile } from '@rtk-query/codegen-openapi'

// используется только как документация — генерация через package.json scripts:
// npm run generate:api
//
// Для CI: сначала экспортировать схему с бэкенда:
//   docker compose -f docker-compose.dev.yml exec backend python manage.py export_openapi > openapi.json
//   openapi-typescript openapi.json -o src/shared/api/generated/schema.d.ts

export default {
  schemaFile: 'http://localhost:8000/api/v1/openapi.json',
  outputFile: 'src/shared/api/generated/schema.d.ts',
} satisfies ConfigFile
