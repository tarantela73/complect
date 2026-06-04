// Генерация TypeScript типов — через package.json scripts:
// npm run generate:api    (требует запущенный бэкенд на :8000)
//
// Для CI без запущенного бэкенда:
//   docker compose -f docker-compose.dev.yml exec backend python manage.py export_openapi --output openapi.json
//   npx openapi-typescript openapi.json -o src/shared/api/generated/schema.d.ts

export default {
  schemaFile: 'http://localhost:8000/api/v1/openapi.json',
  outputFile: 'src/shared/api/generated/schema.d.ts',
}
