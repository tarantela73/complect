# Подготовка host-команд Docker/Buildx

`run_legacy_qualification.execute` и host fixture
`infra.scripts.tests.test_legacy_recovery_cli.run` используют общий
`prepare_host_environment`. Подготовка выполняется до build; штатный
qualification сохраняет committed-source протокол и исходные verdict/exit.

Docker config остаётся исходным: `DOCKER_CONFIG`, `DOCKER_CONTEXT`,
`DOCKER_HOST`, TLS/cert paths и авторизация не копируются и не переключаются.
Helper меняет только расположение служебных записей Buildx. Если
`BUILDX_CONFIG` отсутствует, он создаёт каталог с mode 0700 внутри собственной
области попытки. Для явно заданных `DOCKER_CONFIG` и `BUILDX_CONFIG` выполняется
реальная пробная запись; пустой, отсутствующий или недоступный каталог блокирует
подготовку без fallback. Явные каталоги сохраняются при очистке.

Изолированный Buildx config восстанавливает обычный Docker/context builder.
Если выбран custom builder и отдельный config потерял бы этот выбор,
подготовка отказывает до build: оператор должен явно передать исходный
доступный `BUILDX_CONFIG`. Недоступные или повреждённые данные выбора также
блокируют подготовку. Helper не читает registry credentials для вывода и не
копирует Docker config в доказательства.

Qualification фиксирует identity собственного `host-private` в attempt state.
После остановки процессов и guarded Docker cleanup удаляется только каталог
с прежними device/inode/uid, private mode и ownership token. Recovery повторяет
эту проверку; замена каталога или ошибка удаления блокирует cleanup и PASS.
Первичный exit остаётся исходным. При этом cleanup не удаляет явно заданные
каталоги и не выполняет prune.

Обычные unit tests используют disposable каталоги, без Docker socket.
Отдельная реальная проверка запускается явно из checkout:

```bash
python3 -B -m infra.scripts.tests.verify_docker_host_environment \
  --output /absolute/new/evidence-directory
```

Она использует существующий локальный daemon, собирает только уникально
помеченные scratch images без сети, сверяет image identity перед удалением,
проверяет context и неизменность синтетического auth-config, ранние отказы,
реальную ошибку прав cleanup и последующий успешный повтор. Нужен обычный
не-root пользователь. Внешняя registry-авторизация, production и полный
qualification 133/399 этой проверкой не подтверждаются. Host recovery fixture
проверяется отдельно на своём штатном public CLI.
