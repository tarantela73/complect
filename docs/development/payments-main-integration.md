# Payments gate: интеграция с main

База: 10c440016ac32561369eed03e29927f532dfb5a8. Источник проверенного runner: 6ac57d3a5043ab76df0316cac17aac86e4f9bd90, включая cleanup fix a3c0f03.

Переносятся Payments API/CAS и тесты, ограниченные runtime roles, штатные guarded migrations, IAM expansion/audit-fence/cleanup admission foundation, внешнее управление pytest lifecycle, физическая replica, строгий JSON report и отдельный обязательный CI job. Backend зависимости common/users/contracts/finance нужны существующему Django registry и миграционному графу.

Регистрация приложений и URL/API/ASGI/prod/dashboard поверхности сохранены от main; новые site/reports/map/tasks/requests приложения не подключаются. Frontend, CMS UI и навыки агентов в этот PR не входят. Имеющиеся поля миграционной политики и runtime-привилегии не расширяются; manual-only retirement остаётся gated.

Проверка: make test-payments-gate на свежем PostgreSQL/PostGIS; вся текущая Payments группа, HTTP409, два streaming replica цикла, lifecycle, валидация отчёта и cleanup. Отдельно проверяются реальные положительный и отрицательный CI сценарии и required status main. Проверки IAM expansion и audit fence относятся к этой foundation-интеграции.

Это локальная/CI приёмка ограниченного стенда. Production RLS, ERP acceptance и Kong/JWT/RBAC E2E не подтверждены. Применение миграций в рабочей или production базе не разрешается данным PR.
