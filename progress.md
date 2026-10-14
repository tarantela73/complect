# Состояние Harness

## 2026-10-15 — Payments CI: порядок mounts

Первый реальный GitHub positive gate прошёл все бизнес-фазы, но cleanup вернул BLOCKED_STAND: Docker перечислял одинаковые mounts в разном порядке. Narrow Payments adapter канонизирует порядок, все поля fingerprint проверяются точно. RED/GREEN и семь field mutation checks PASS; свежий local fullgate all11PASS. Новый Harness receipt `docs/agents/harness-evidence/20261014T210629866457Z.json`. Старый receipt сохранён в evidence_history.

Repo теперь public, защита main отсутствует (404), rulesets пусты. Негативный CI38635709596 корректно сообщил injected prepare; positive retry ожидается. #16/#21 остаются открыты до CI/required-status evidence. Evidence `.dev/test-artifacts/payments-ci-mount-order-20261015/`. ERP/production/Kong NOT_RUN.


## 2026-10-15 — Payments #16–21

feat-005 done: #17–20 подтверждены текущими working-final и candidate-final gate, каждый 55 Payments + 5 HTTP, два физических replica-цикла, lifecycle и cleanup. Негативный отказ после lag восстанавливает replay и синхронизацию; prepare/foreign_target/socket/cleanup также дают честный BLOCKED_STAND. Свежий Harness receipt: `docs/agents/harness-evidence/20261014T204159580646Z.json`.

feat-014 blocked: отдельный Payments workflow реализован и проверен локально. Реальные CI-прогоны публикуются тегами на проверенный коммит. Required merge status недоступен: API403 для private repository без Pro, rulesets пусты. #16/#21 не получают полную приёмку. Evidence `.dev/test-artifacts/payments-ci-16-21-20261015/`. ERP/production/Kong acceptance NOT_RUN. После окончательного HEAD/index snapshot захватывается явно заново.


## 2026-10-15 — #22: актуальная приёмка Harness

feat-001: done. Действующий receipt `docs/agents/harness-evidence/20261014T185901568539Z.json` получен фиксированным Docker gate: 26 Python + 21 Node, skips 0, exit 0, источники неизменны. Предыдущий receipt включён в evidence_history; исторические JSON/логи сохранены. После обновления общего snapshot обе штатные точки запуска прошли повторно. Доказательство относится к рабочему дереву, включая незакоммиченные исходники, а не к отдельному коммиту. ERP_ACCEPTANCE=NOT_RUN. Итог: `docs/agents/harness-evidence/restore-22-20261015.md`; свежие логи `.dev/test-artifacts/harness-restore-22-implement-20261015/`. После изменения HEAD/индекса snapshot захватывается явно заново.


Дата: 2026-10-11. Active Feature: `feat-001`, done.

Согласованный commit snapshot проверен в Docker: 26 Python и 21 Node тест, без skips. Настоящий receipt находится в feature_list.json. Runtime/IAM/PII изменения текущего working tree сохраняются отдельно. ERP_ACCEPTANCE: NOT_RUN. Доказательства предыдущих сессий сохранены в evidence_history.

2026-10-15 feat-013: implementation PASS, PASS_LOCAL_GATE. Infra261/259passed/2conditional skips +pytest60; Ruff/mypy/actionlintPASS. Fresh original133 all399phasesPASS/no skips/failures; preflight/post-run source/copy/seven-result envelope/runcleanup/fullstandcleanupPASS. Real SIGTERM/recovery/cleanup proof retained; interruption reason preserved, unknown inventory blocked. StandardsPASS,SpecPASS. Harness BLOCKED_STALE_SNAPSHOT; earlier receipts/snapshot unchanged. GitHub Actions/push/ERP/production/Kong acceptance NOT_RUN. Evidence .dev/test-artifacts/infra-review-fixes-20261015/report.md.
