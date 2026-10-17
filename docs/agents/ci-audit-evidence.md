# Локальная проверка доказательств CI

Контракт GitHub #47, первая часть #46. Получение данных CI выполняет аудитор. Этот модуль проверяет уже скачанный пакет без сети и не меняет входы. Проверка подтверждает локальную целостность и согласованность; она не является независимой аттестацией GitHub.

Для ожидания terminal conclusions и возобновления аудита следуй
[инструкции аудитору](ci-auditor.md).

## Запуск и результат

Из корня репозитория:

```bash
python3 -B -m infra.scripts.ci_audit_evidence \
  --requirements /tmp/audit-input/requirements.json \
  --report /tmp/audit-package/report.json \
  --root /tmp/audit-package \
  --output /tmp/audit-result.json
```

Выходной файл должен быть новым, находиться вне пакета и отличаться от входов. Существующие файлы не перезаписываются. Каталог результата должен существовать. Stdout содержит статус, число jobs и путь результата; подробности находятся в отдельном JSON формата `complect-ci-audit-result-v1`.

| Статус | Exit | Условие |
| --- | --- | --- |
| PASS | 0 | Все обязательные jobs завершились успешно, полный пакет проверен |
| FAIL | 1 | Нарушение целостности/привязки, некорректный контракт или полный пакет отрицательного CI |
| BLOCKED_EVIDENCE | 2 | Нет обязательного job, файла или метаданных завершённого CI |
| PENDING | 3 | CI ещё выполняется; недостающие результаты ожидаются |

Нарушение целостности имеет приоритет над неполнотой и ожиданием. Отсутствующий целиком обязательный job остаётся BLOCKED_EVIDENCE; его состояние не угадывается по соседним jobs. Для каждого найденного job сохраняются исходные `status`, `conclusion`, `verdict`, `exit_code`, проверенные пути/хеши, ошибки и недостающие доказательства. SUCCESS без полного пакета не даёт PASS. Отрицательный conclusion сохраняется даже при неполном пакете; при этом статус аудита может быть BLOCKED_EVIDENCE.

## Независимый список требований

Координатор задаёт требования до проверки пакета. Список нельзя получать фильтрацией найденных jobs. JSON имеет `format: complect-ci-audit-requirements-v1` и непустой `jobs`. Для новых Infrastructure/Payments добавляется `profile: ci_gate_producer`; тогда каждый job обязан перечислить `receipt`, `runner_receipt`, `command_log` и `gate`, а report обязан повторить этот профиль. Без profile сохраняется generic v1 для исторических пакетов. Каждый элемент содержит:

| Поле | Значение |
| --- | --- |
| name | Уникальное имя job, 1–100 символов из букв ASCII, цифр, `_`, `.`, `-` |
| repository | `owner/repository` |
| run_id, run_attempt, job_id | Положительные целые числа; булевы значения запрещены |
| head_sha, base_sha | Полные 40-символьные lowercase Git SHA публикуемого head и базы |
| checkout_sha, tree_sha | Полные SHA фактического проверяемого checkout и его дерева |
| artifacts | Непустой список уникальных имён обязательных artifacts, включая `receipt` |

Повторение имени job или сочетания repository/run/attempt/job запрещено. Для merge checkout координатор отдельно закрепляет фактические checkout/tree; равенство head и checkout не требуется. Proof и snapshots должны описывать именно эти checkout/tree. Если merge-дерево отличается от head-дерева, доказательства head-дерева его не заменяют.

## Скачанный отчёт

JSON имеет `format: complect-ci-audit-report-v1` и `jobs`. Для каждого обязательного job предоставляются его `name`, все восемь полей идентичности из требований, а также:

- `status`: `queued`, `in_progress`, `waiting`, `pending`, `requested` или `completed`.
- `conclusion`: null во время исполнения; для completed — `success`, `failure`, `cancelled`, `timed_out`, `neutral`, `skipped`, `action_required`, `stale` или `startup_failure`.
- `verdict`, `exit_code`: исходные результат и целый exit. PASS требует `verdict: PASS` и exit 0, согласованных с receipt.
- `log`: descriptor журнала и `checkout_line` — номер строки с полным checkout SHA, начиная с 1.
- `source_proof`: descriptor привязанного proof.
- `artifacts`: отображение обязательных имён в descriptors.

Descriptor содержит относительный `path` внутри пакета и lowercase SHA-256 в `sha256`. Абсолютные пути, `..`, symlinks, специальные файлы и неоднозначное использование одного файла вместо разных обязательных доказательств отклоняются. Пропущенные jobs блокируют PASS; дубли и неожиданные jobs отклоняются. Поле `ci_logs_downloaded` не используется как доказательство.

Checkout проверяется по строке вывода следующей команды в скачанном job log:

```text
[command]/usr/bin/git log -1 --format=%H
<полный checkout SHA>
```

Допускаются префиксы job/step/time от GitHub и другой путь исполняемого git. Номер `checkout_line` указывает на SHA; предыдущая строка должна содержать команду. Весь журнал хешируется через существующий authenticated reader evidence bundles; для поиска checkout читается только первый 1 МиБ. Если checkout вне этого префикса, результат BLOCKED_EVIDENCE. Данные за пределами префикса также защищены проверкой полного хеша.

## Source proof и receipt

Proof — JSON с `format: complect-ci-source-proof-v1`, `status: PASS`, всеми восемью полями идентичности job, descriptors `source_before`, `source_after` и отображением `artifacts`. Привязанные обязательные artifacts должны совпадать с descriptors отчёта.

Оба snapshot — разные файлы с одинаковым SHA-256. Каждый содержит `head`, `tree`, совпадающие с checkout/tree требований, и непустой `files`: относительный путь исходника → объект с lowercase SHA-256 в `sha256`. Дополнительные сведения о mode/index допустимы и участвуют в хеше файла. Изменение snapshot между началом и концом даёт FAIL. Это проверка сохранённых доказательств, а не повторное исполнение проверки или восстановление Git-дерева по рабочему каталогу.

Обязательный `receipt` — исходный JSON с `status` и целым `exit`; они должны точно совпадать с `verdict` и `exit_code` отчёта. Другие обязательные artifacts проверяются как байты и не обязаны быть JSON. JSON-документы ограничены 1 МиБ, повторяющиеся ключи и не-объекты отклоняются.

Исторические receipts не переписываются под новый формат. Отчёт или proof без обязательных сведений не повышается автоматически до нового контракта. Старый декларативный audit без формата и реестра jobs получает BLOCKED_EVIDENCE. Если producer не сохранил job/attempt, checkout/tree или исходный exit, сначала нужно получить настоящее доказательство; присваивать эти значения задним числом запрещено.

## Проверка изменений

Публичная граница — `verify_ci_audit` и CLI модуля. Поведенческие тесты используют копии/модели пакетов и проверяют статусы, содержимое evidence, сохранение исходных результатов и неизменность входов. Для изолированных прогонов: Docker без сети, checkout только для чтения, временные файлы в tmpfs. Проверка настоящего исторического пакета имеет отдельный результат и не заменяет новые тесты реализации.


## Точка продолжения

GitHub #50 добавляет `complect-ci-audit-continuation-v1` в тот же модуль;
обычный audit/result-v1 и существующие exit-коды сохраняются. Публичный
`make_ci_audit_continuation(requirements, report, root, continuation=previous)` создаёт объект для
PENDING или BLOCKED_EVIDENCE. `verify_ci_audit(..., continuation=point)`
проверяет его перед оценкой свежего report. Сеть и ожидание принадлежат агенту.

Точка содержит ровно `format`, `status`, `requirements`, `report`, `checked`,
`missing`, `history`. В requirements закреплены все обязательные jobs/artifacts и восемь
полей identity. Report содержит только поля локального контракта, descriptors
и исходные status/conclusion/verdict/exit; посторонние поля API не копируются.
`checked` и `missing` — отображения имени job в проверенные descriptors и список
недостающих данных. `history` хранит прежние наблюдения той же привязки: status,
report, checked, missing. При очередном сохранении после resume проверенные
файлы не теряются, даже если свежий report больше не содержит прежний descriptor;
при продолжении перечитываются и история, и последнее наблюдение. Неизменное
наблюдение не добавляет дубликат в историю. Состояние completed без conclusion остаётся
BLOCKED_EVIDENCE; такая точка также может быть сохранена до получения metadata.

Пример сохранения, все выходы новые и вне package:

```bash
python3 -B -m infra.scripts.ci_audit_evidence \
  --requirements /tmp/audit/requirements.json --report /tmp/package/report.json \
  --root /tmp/package --output /tmp/audit/poll-01.json \
  --save-continuation /tmp/audit/pending-01.json
```

Stdout возвращает путь и фактический `continuation_sha256`; сохрани pin отдельно
от checkpoint. Для продолжения нужны свежий report и исходные независимые
requirements. Pin берётся из предыдущего producer/index, а не пересчитывается
по текущему непроверенному файлу:

```bash
python3 -B -m infra.scripts.ci_audit_evidence \
  --requirements /tmp/audit/requirements.json --report /tmp/package/report-02.json \
  --root /tmp/package --output /tmp/audit/poll-02.json \
  --continuation /tmp/audit/pending-01.json --continuation-sha256 "$CHECKPOINT_SHA256"
```

CLI аутентифицирует весь checkpoint по pin; JSON ограничен 1 МиБ, повторы ключей
и symlinks отклоняются. Формат и привязки проверяются заново. Прежний report и
ранее проверенные файлы также перечитываются: изменение хеша даёт FAIL,
недоступный ранее проверенный файл — BLOCKED_EVIDENCE. Недостающие metadata
прежнего PENDING сами по себе не блокируют свежий полный успешный report.
Исторические state/receipts не переписываются. Изменение run/attempt/job или
source identity требует отдельного checkpoint; новый полный audit без старой
точки возможен только с новыми независимыми требованиями.

`--save-continuation` не пишет новую точку для PASS/FAIL. Если проверка прежних
доказательств не прошла, новая точка также не создаётся. Существующие выходы,
входы и совпадающие output/checkpoint отклоняются до записи. Checkpoint создаётся
с mode 0600; ошибка сохранения даёт ненулевой exit. API проверяет структуру и
доказательства объекта; при чтении сохранённого файла используй CLI с независимым
pin. Это локальная целостность, а не защищённая аттестация GitHub.
